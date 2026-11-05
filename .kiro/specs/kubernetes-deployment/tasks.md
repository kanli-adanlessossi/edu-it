# Implementation Plan: kubernetes-deployment

## Overview

This plan implements the three milestones of the design: A (Kubernetes sandbox provider), B (Helm chart and images) and C (operations, CI and documents). The implementation languages follow the design: Python 3 for domain, infrastructure, application, api and worker code and for tools and tests (Hypothesis for property tests), TypeScript for `apps/web`, and Helm templates, YAML and Dockerfiles for the chart and images.

The order follows the Delivery sequence of the design. Decisions that can change the design come first (draft ADRs and the day-one pod layout spike, risk R1). The spike tests layout B first (no shared process namespace, freeze inside the learner container through `exec`) and falls back to layout A only if B fails the isolation checks (Req 16.12). Only the pod builder, the `as_user` table, its Property 2 test and the provider tasks wait for the layout recorded in ADR 0009. Everything else is gated only on its real data and file dependencies, so domain foundations, migrations, observability, the chart skeleton, the Dockerfiles, the kind harness and the CI skeleton start in parallel at the beginning. A walking skeleton on kind proves the real path early: harness, production images, a minimal chart, an early CI workflow, the real-API contract test (risk R2) and the shared provider suites on a real API server. Application behavior, Api and web behavior and observability follow. The full chart, supply chain, kind test suites, the complete CI workflow, documents (written after measured data exists), traceability and the Phase 5 report close the plan.

The requirements already contain the amendments for the open-question resolutions OQ-1 to OQ-8, so no further requirements edit is planned. ADRs 0009, 0010 and 0011 are written as drafts first (task 1.1) and finalized in the tasks where the owning code lands (5.10, 12.15, 13.5), together with the matching steering updates, as required by the working agreements. Business numbers go in `domain/config.py`, the domain imports no framework, time comes from the injected `Clock`, and every external system sits behind a port with a fake, as required by the quality steering.

Traceability is incremental. Task 6.17 extends `tools/check_traceability.py` so that each test declares the criteria it verifies with a `# Claims: <ids>` tag next to the test (in addition to the existing table). Every test task in this plan tags its tests with the requirement ids listed on the task, so no shared claims file is written by several tasks. Task 17.1 closes any remaining gap.

Steering that governs this plan: #[[file:.kiro/steering/deployment.md]], #[[file:.kiro/steering/labs.md]], #[[file:.kiro/steering/quality.md]], #[[file:.kiro/steering/working-agreements.md]].

## Tasks

- [x] 1. Decisions and day-one spike
  - [x] 1.1 Write draft ADRs 0009, 0010 and 0011 and the matching steering updates
    - Create `docs/adr/0009-kubernetes-sandbox-provider.md` (exec through the API server, three-container identity model, deterministic pod name, OQ-1, OQ-2, OQ-5, OQ-6), `docs/adr/0010-helm-chart-conventions.md` (values contract, hooks and bootstrap resources, extension kinds, OQ-3, OQ-4, OQ-8) and `docs/adr/0011-image-supply-chain.md` (distroless bases, scan threshold, SBOM, keyless signing, provenance, admission policy)
    - Each ADR has a context section, a decision section and a consequences section, and carries the status `Draft`
    - Add the matching draft rules to `.kiro/steering/labs.md` and `.kiro/steering/deployment.md` in the same change
    - The ADRs are finalized (status `Accepted`) in tasks 5.10, 12.15 and 13.5, when the owning code lands
    - _Requirements: 1.8, 2.1, 2.6, 5.1, 5.4, 7.2, 7.3, 7.4, 8.8, 11.4, 12.1, 12.3, 12.5, 12.7, 12.8, 12.10_

  - [x] 1.2 Run the day-one pod layout spike on kind (risk R1)
    - Create `deploy/kind/spike-config.yaml` (one node, Pod Security Admission restricted enforced on the test namespace, `podPidsLimit` set in the kubelet configuration) and `tests/k8s/spike_pod_layout_kind.py`
    - Test layout B first: three containers (`learner`, `grader`, `owner`) with distinct non-root UIDs, no `shareProcessNamespace`, the freeze run inside the `learner` container through `exec`, and helper identities (`grader`, `owner`) kept only where they work with all capabilities dropped
    - Prove under restricted PSA with all capabilities dropped: (1) the freeze command used by `GradeRun` stops every learner process and the stop is verified within the freeze bound, (2) a learner process cannot list, read or stat the `/grader` mount and cannot read `/proc/<pid>/environ`, `cmdline` or `fd` of grader or owner processes, (3) the Worker ServiceAccount can exec into each container with only `create` and `get` on `pods/exec` and the Api ServiceAccount can exec only into `learner`, (4) a command killed on `timeout_s` leaves no process in its group, (5) a fork bomb in the learner container is stopped by the PID limit
    - Fall back to layout A (shared process namespace) only if B fails the isolation checks and A passes all of them, then to layout D (layout C is recorded only)
    - Record the chosen layout, the evidence and the rejected layouts in `docs/adr/0009-kubernetes-sandbox-provider.md` before any provider code is built (Req 16.12)
    - GATE: only the pod builder (4.3), the `as_user` table (4.3), the Property 2 test (4.4) and the provider tasks (5.2 to 5.10) depend on this task. Layout-independent work (4.1, 4.6, 5.1 and all other tasks) does not wait for it. If the layout is not B, tasks 4.3 and 4.4 and the `as_user` table follow the recorded layout, and the grading semantics stay unchanged
    - _Requirements: 2.2, 2.6, 4.7, 5.1, 5.4, 5.5, 5.6, 16.10, 16.12_

- [x] 2. Domain foundations for Milestone A
  - [x] 2.1 Add domain types and constants
    - Add `ProvisionCapacityError(ProvisionError)` to `ports/sandbox.py` and `SessionStatus.ENDED_BY_INFRASTRUCTURE`
    - Add `LAB_DESTROY_TIMEOUT`, `LAB_PROVISION_CAPACITY_BACKOFF`, `K8S_READY_CEILING`, `LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND`, `LAB_INFRA_LOSS_MARK_BOUND`, `LAB_EVICTED_POD_REMOVAL_BOUND` the reconnect schedule constants and the drain period and preStop delay defaults and bounds (Req 14.2, 14.12) to `domain/config.py` (the helper container constants are added in task 4.3, because they depend on the chosen layout)
    - Extend `validate_lab_config` (positive values, capacity backoff at least `LAB_PROVISION_MIN_INTERVAL`, ready ceiling at least the provisioning timeout)
    - Define the `LabMetrics` protocol with a no-op fake, and the `RejectionKind`, `CapacityDecision`, `RuntimeDecision` and `CniOutcome` types
    - _Requirements: 1.5, 6.2, 6.3, 19.2, 22.1, 22.6_

  - [x] 2.2 Implement the pure domain rules
    - Add `capacity_decision`, `runtime_guard`, `infra_loss_transition`, `select_reaper_actions`, `reconnect_delays` and `cni_check_outcome` under `domain/labs/`
    - Write the shared reconnect vectors JSON file that the TypeScript client also reads
    - `select_reaper_actions` uses only persisted rows and the provider listing and never selects an active session
    - _Requirements: 2.9, 2.10, 6.6, 6.7, 11.9, 11.10, 11.11, 11.12, 14.5, 19.2, 19.3, 19.4, 19.5, 19.7, 19.8, 20.1, 20.3, 20.7, 22.6, 22.7_

  - [x] 2.3 Write property test for runtime guard
    - **Property 11: Runtime guard decision**
    - **Validates: Requirements 2.9, 2.10**

  - [x] 2.4 Write property test for reaper selection
    - **Property 12: Reaper selection**
    - **Validates: Requirements 6.6, 6.7, 19.5, 20.3, 20.7**

  - [x] 2.5 Write property test for capacity requeue decision
    - **Property 13: Capacity requeue decision**
    - **Validates: Requirements 22.6, 22.7**

  - [x] 2.6 Write property test for infrastructure loss transition
    - **Property 15: Infrastructure loss transition**
    - **Validates: Requirements 19.2, 19.3, 19.4, 19.7, 19.8**

  - [x] 2.7 Write property test for reconnect schedules
    - **Property 17: Reconnect schedules**
    - **Validates: Requirements 14.5, 20.1**

  - [x] 2.8 Write property test for CNI check outcome
    - **Property 24: CNI check outcome**
    - **Validates: Requirements 11.9, 11.10, 11.11, 11.12**

- [x] 3. Migrations and backward compatibility
  - [x] 3.1 Add the additive migration and the transactional runner
    - Add an additive migration for the `ended-by-infrastructure` status and the `capacity` provisioning attempt kind
    - Implement `python -m edu_it_infrastructure.migrate`: each migration runs in a single PostgreSQL transaction and rolls back on any failing statement
    - On failure log the migration id and the database error text, and exit non-zero
    - Make reads in the previous release tolerate an unknown status value
    - _Requirements: 21.2, 21.3, 21.6_

  - [x] 3.2 Write property test for failed migration
    - **Property 32: Failed migration leaves the schema unchanged**
    - **Validates: Requirements 21.2, 21.3**

  - [x] 3.3 Add `tools/check_migrations_compat.py`
    - Fail when a migration drops or renames a column or table in the same release that stops using it
    - _Requirements: 21.6_

  - [x] 3.4 Write unit tests for the compatibility check
    - Cover add-only, drop and rename cases
    - _Requirements: 21.6_

- [x] 4. Kubernetes provider pure logic
  - [x] 4.1 Add backend parsing and selection
    - Add `SandboxBackend.KUBERNETES` and `parse_sandbox_backend` (error names the invalid value and lists `docker`, `kubernetes`, `fake`)
    - Call it at Api and Worker startup before serving or consuming
    - _Requirements: 1.2, 1.3_

  - [x] 4.2 Write property test for backend selection
    - **Property 1: Sandbox backend selection**
    - **Validates: Requirements 1.2, 1.3**

  - [x] 4.3 Implement the pure pod builder `k8s_pod_spec.py` for the layout chosen in task 1.2
    - Add `K8sSettings` and `build_lab_pod(spec, settings)` with the deterministic name, labels, containers, security contexts, resources, `activeDeadlineSeconds`, runtime class, node selector, tolerations (including node-loss `tolerationSeconds`), `shareProcessNamespace` as decided by the spike, and the `edu-it.io/pid-limit` annotation (OQ-6)
    - Add `K8S_HELPER_CPU_MILLI` and `K8S_HELPER_MEMORY_BYTES` to `domain/config.py` if the chosen layout has helper containers
    - Add the `as_user` to container mapping table for the chosen layout
    - _Requirements: 1.4, 1.6, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 19.1_

  - [x] 4.4 Write property test for pod hardening and placement
    - **Property 2: Pod manifest satisfies the hardening and placement rules**
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**

  - [x] 4.5 Write property test for pod identity
    - **Property 3: Pod identity is one per session**
    - **Validates: Requirements 1.4, 1.6, 20.5**

  - [x] 4.6 Implement rejection classifier and error mapper `k8s_errors.py`
    - `classify_create_rejection` and `map_api_exception` as pure functions
    - Keep raw API text out of exception messages shown to learners
    - _Requirements: 1.11, 1.12, 2.11, 2.12, 3.8, 22.1_

  - [x] 4.7 Write property test for create-rejection classification
    - **Property 4: Create-rejection classification**
    - **Validates: Requirements 2.11, 2.12, 3.8, 22.1**

  - [x] 4.8 Write property test for API error mapping
    - **Property 10: API errors map to sandbox errors**
    - **Validates: Requirements 1.11, 1.12**

  - [x] 4.9 Write unit tests for the pod builder and classifier
    - Golden manifest file for the chosen layout, real API server messages for each rejection kind
    - _Requirements: 2.2, 2.6, 2.11, 2.12_

  - [x] 4.10 Implement the termination grace arithmetic
    - Add `min_termination_grace(drain, prestop)` (drain plus preStop delay plus 10) and `check_termination_grace(drain, prestop, grace)` in a framework-free domain module, with a rejection that names the three values
    - The Chart validation (task 12.11) and the chart tests reuse the same rule
    - _Requirements: 14.2, 14.4, 14.12_

  - [x] 4.11 Write property test for termination grace arithmetic
    - **Property 34: Termination grace arithmetic**
    - **Validates: Requirements 14.2, 14.4, 14.12**

  - [x] 4.12 Implement the values consistency check
    - Add `tools/check_values_consistency.py` with `check_values_consistency(values, lab_session_ttl)`: accept a production Values_File only if `labs.sessionTtlSeconds` equals `LAB_SESSION_TTL`, the WebSocket read and send timeouts are each at least that constant, and no `kindTest.sessionTtlSeconds` key exists
    - Add `tests/chart/test_values_consistency.py` that loads the four Values_Files once they exist (skipped only while a file is absent, with the skip removed by task 6.7)
    - _Requirements: 9.4, 9.5, 16.11, 2.5_

  - [x] 4.13 Write property test for production TTL consistency
    - **Property 33: Production TTL values match the domain constant**
    - **Validates: Requirements 9.4, 9.5, 16.11, 2.5**

- [x] 5. Kubernetes sandbox provider over FakeK8sApi
  - [x] 5.1 Implement `FakeK8sApi`
    - Model pods, quota, PodSecurity, RuntimeClass, scheduling and delays on the injected `Clock`
    - Define the `K8sApiPort` adapter interface that both the fake and the real client implement, so the contract test of task 6.22 can run the same scenarios on both
    - _Requirements: 1.5, 2.11, 2.12, 22.8_

  - [x] 5.2 Implement `KubernetesSandboxProvider` lifecycle
    - `provision` (adopt on 409 with the same session label, wait `min(LAB_PROVISION_TIMEOUT, K8S_READY_CEILING)`, delete and wait on failure, `ProvisionCapacityError` for quota or Pending Unschedulable), `destroy`, `list_sandboxes`, `health`
    - Support the warm-pool extension point without changing the port
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 1.8, 2.11, 2.12, 6.1, 6.2, 6.3, 6.4, 6.5, 6.10, 22.1, 22.3, 22.8_

  - [x] 5.3 Implement exec, copy and terminal adapter `k8s_exec.py`
    - `exec` with process-group kill and `timed_out`, `copy_in` through the owner identity, `copy_out`, `attach_terminal` returning a `TerminalChannel` with `read`, `write`, `resize` and `close`, following the layout chosen in task 1.2
    - Raise `SandboxGone` when the pod is gone
    - Wire the methods into `KubernetesSandboxProvider`
    - _Requirements: 1.1, 1.10, 1.11, 1.12, 5.1, 5.2, 5.6_

  - [x] 5.4 Wire the provider and extend the test suites
    - Add the `kubernetes` branch to `build_sandbox_provider`
    - Add the Kubernetes provider (over `FakeK8sApi`) to the Sandbox_Port contract suite with zero skips
    - Parametrize the labs isolation, grading isolation, tamper-resistance and check-abuse suites so they can run with the Kubernetes provider substituted, with zero additional skips or exclusions (the real-cluster run is task 6.23)
    - _Requirements: 1.1, 1.2, 1.9, 5.7_

  - [x] 5.5 Write property test for failed provisioning
    - **Property 6: A failed provisioning leaves no pod**
    - **Validates: Requirements 1.5, 2.11, 2.12, 6.2, 22.3, 22.8**

  - [x] 5.6 Write property test for provisioning idempotency
    - **Property 7: Provisioning is idempotent**
    - **Validates: Requirements 20.5**

  - [x] 5.7 Write property test for listing
    - **Property 8: Listing returns only managed pods**
    - **Validates: Requirements 1.7**

  - [x] 5.8 Write property test for destroy
    - **Property 9: Destroy is bounded and idempotent**
    - **Validates: Requirements 6.3, 6.4, 6.5**

  - [x] 5.9 Write unit tests for exec and terminal
    - Timeout kill of the process group, terminal channel after pod loss
    - _Requirements: 1.10, 1.11, 1.12, 5.6_

  - [x] 5.10 Finalize ADR 0009 and the labs steering
    - Set `docs/adr/0009-kubernetes-sandbox-provider.md` to `Accepted` and align it with the shipped provider (layout from task 1.2, exec through the API server, deterministic pod name)
    - Align `.kiro/steering/labs.md` with the final rules in the same change
    - _Requirements: 1.8, 2.1, 2.6, 5.1, 5.4_

- [x] 6. Walking skeleton on kind
  - [x] 6.1 Add the kind harness and its prerequisites
    - `deploy/kind/kind-config.yaml` (one control-plane, two workers, labs label `edu-it.io/pool=labs` and taint `edu-it.io/labs=true:NoSchedule` on one worker, default CNI disabled, kubelet `podPidsLimit` set from the Docker PID limit constant, which the spike of task 1.2 proves) and `tools/kind_harness.py`; this task does not wait for the spike
    - The harness installs, in order: Calico, metrics-server, ingress-nginx, the CRDs for each of `ExternalSecret`, Gateway API and `ServiceMonitor` that the Chart renders, and a local registry at `localhost:5001`, and it preloads the lab images into the node containerd store so Sandbox_Ready_Time excludes registry pulls
    - The harness prints the Kubernetes API server address for the kind values override (used by task 6.8)
    - _Requirements: 16.1, 16.3, 16.6, 16.9_

  - [x] 6.2 Add Dockerfiles for api, worker and migrations
    - `deploy/images/<name>/Dockerfile`, builder stage plus distroless `nonroot` final stage with a numeric user above 0, migrations as a separate target with the `migrate` entrypoint
    - _Requirements: 12.3, 12.4_

  - [x] 6.3 Add the web Dockerfile
    - Next.js standalone output, distroless Node `nonroot` final stage, numeric user above 0
    - _Requirements: 12.3, 12.4_

  - [x] 6.4 Add image check tools
    - `tools/check_image_contents.py` (forbidden binaries `sh`, `bash`, `apk`, `apt`, `apt-get`, `dnf`, `yum`, `pip` and numeric non-root user from the image config and layer list), `tools/image_refs.py` with `is_pinned_image_ref`, and `tools/check_image_refs.py` (digest-only references in the chart values)
    - _Requirements: 12.3, 12.4, 12.11_

  - [x] 6.5 Write unit tests for the image check tools
    - Accept and reject cases for each forbidden binary and for tag-only references
    - _Requirements: 12.3, 12.4, 12.11_

  - [x] 6.6 Write property test for image references
    - **Property 25: Image references use a sha256 digest**
    - **Validates: Requirements 7.11, 7.12, 12.11**

  - [x] 6.7 Create the chart skeleton, values and schema
    - `Chart.yaml`, `values.yaml` and the four Values_Files (`values-dev.yaml`, `values-eks.yaml`, `values-aks.yaml`, `values-onprem.yaml`) with every key of the design values contract and no secret values
    - `values.schema.json` with required keys, enums, numeric ranges, the digest pattern, and an optional `kindTest` object that is allowed only for the kind test values file
    - `_helpers.tpl` with the name, label and image helpers that all later templates share, so later tasks do not edit it again until task 12.11
    - Remove the skip from `tests/chart/test_values_consistency.py` now that the four files exist
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.7, 7.8, 7.11, 7.12_

  - [x] 6.8 Add the `networkPolicy.kubernetesApi.cidrs` values key
    - Add the key to `values.yaml`, `values.schema.json` (non-empty list of CIDRs or endpoint addresses) and all four Values_Files with per-Target placeholder values and a comment on how to find the real value (`kubectl get endpoints kubernetes -n default`)
    - Add a `fail` helper that names `networkPolicy.kubernetesApi.cidrs` when the list is empty and NetworkPolicy objects render
    - The kind install passes the address printed by the harness (task 6.1) through a `--set` override, wired in task 6.14
    - _Requirements: 11.5, 11.13, 11.14_

  - [x] 6.9 Add the PriorityClass
    - `priorityclass.yaml` rendering the Platform_Priority_Class, the keys `priorityClass.{name,value}` and `labs.podPriorityValue` in `values.yaml`, the schema and the Values_Files, and the `fail` helper `priorityClass.value > labs.podPriorityValue` that names both values
    - Lab pods use `labs.podPriorityValue` (task 4.3 reads it from `K8sSettings`)
    - _Requirements: 8.12_

  - [x] 6.10 Add namespaces, ServiceAccounts and RBAC
    - Namespaces with PSA enforce, audit and warn labels when `namespaces.manage` is `create`, none when operator-managed
    - One ServiceAccount each for Api, Worker and Migration_Job with `automountServiceAccountToken: false`
    - Worker `Role` and `RoleBinding` in `labs`, Api `Role` and `RoleBinding` in `labs` (OQ-1), no cluster-scoped grants
    - _Requirements: 2.1, 4.1, 4.2, 4.3, 4.6, 4.7, 7.9, 7.10, 10.8_

  - [x] 6.11 Add the labs network policies, quota, limit range and runtime class
    - Default-deny and Labs_Allowlist in `labs` (ingress from Api for terminal attach, from Worker for exec, copy and health), optional DNS egress rule that is off by default
    - `ResourceQuota`, `LimitRange` and optional `RuntimeClass` from values
    - _Requirements: 2.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

  - [x] 6.12 Add the dev datastores and dev secrets
    - `dev-datastores.yaml` (small PostgreSQL and Redis StatefulSets, rendered only when `devDatastores.enabled`) with a guard, written inline in the template (not in `_helpers.tpl`), that fails rendering when it is true while `environment` is `production`
    - `secrets-dev.yaml` with plain Secret placeholders, rendered only when `devSecrets.enabled`
    - _Requirements: 10.9, 16.9_

  - [x] 6.13 Add the minimal Api and Worker workloads and the migration hook
    - `deployment-api.yaml`, `deployment-worker.yaml`, `services.yaml` and `job-migration.yaml` (pre-install and pre-upgrade hook running `python -m edu_it_infrastructure.migrate`), with image digests, SecretSource volumes, basic probes, resources and the restricted security context, enough for a kind install and one provisioning round trip
    - The full workload settings are added in task 12.1
    - _Requirements: 8.5, 8.6, 8.8, 21.1_

  - [x] 6.14 Add the Helm test hook, the provisioning smoke test and the kind job
    - `templates/tests/smoke.yaml` as a Helm `test` hook pod that uses the api image with a Python entrypoint (no shell) to check the Api and Worker readiness endpoints
    - `tests/k8s/test_smoke_kind.py`: install on kind, passing the Kubernetes API address printed by the harness through `--set networkPolicy.kubernetesApi.cidrs=...`, run `helm test`, then provision and destroy one Lab_Pod through `build_sandbox_provider` and assert that zero Lab_Pods remain
    - Add the kind job to `.github/workflows/deployment.yml`: run the harness, install the Chart, wait up to 600 s, run the smoke test; on pull-request and other branch runs it uses the local-registry digests and records that signature verification was skipped (Req 15.8)
    - _Requirements: 1.4, 1.5, 6.3, 15.4, 15.8, 16.1, 11.13_

  - [x] 6.15 Add `tools/check_ci_pins.py`
    - Report every third-party `uses:` reference that is not a 40-character commit SHA
    - _Requirements: 15.11_

  - [x] 6.16 Write property test for action pinning
    - **Property 26: Workflow actions are pinned by SHA**
    - **Validates: Requirements 15.11**

  - [x] 6.17 Extend `tools/check_traceability.py` for incremental claims
    - Add `compute_traceability_gaps` for Requirements 1 to 25, collecting claims from the existing table and from `# Claims: <ids>` tags in test files, with a non-zero exit listing each unclaimed or unknown id
    - _Requirements: 26.1, 26.2_

  - [x] 6.18 Write property test for traceability gaps
    - **Property 30: Traceability gaps**
    - **Validates: Requirements 26.1, 26.2**

  - [x] 6.19 Extend `tools/phase_report.py` for phase 5
    - Add `report_status`, per-milestone command and outcome listing (passed, failed, not run), Not verified section, next-steps ranking
    - _Requirements: 26.3, 26.4, 26.5, 26.6, 26.7_

  - [x] 6.20 Write property test for report status
    - **Property 31: Report status**
    - **Validates: Requirements 26.4, 26.5, 26.6**

  - [x] 6.21 Add the early CI workflow `.github/workflows/deployment.yml`
    - Jobs: unit and property tests (kind-marked tests excluded), `helm lint` and `helm template` for each Values_File that exists, image build (amd64) to the local registry with a vulnerability scan that fails on a finding at the threshold and on a scan error, `check_ci_pins` and `check_traceability`
    - Pull-request and other branch runs push only to the local registry, never sign and never push to a real registry, and the run output records that signature verification was skipped (Req 15.8)
    - Top-level `permissions: contents: read`, every third-party action pinned to a full 40-character commit SHA
    - Task 6.14 adds the kind job, and tasks 13.1, 13.4, 15.1, 15.2 and 16.5 extend this file
    - _Requirements: 15.1, 15.2, 15.3, 15.8, 15.11, 7.5, 7.6_

  - [x] 6.22 Add the real-API contract test (risk R2)
    - `tests/k8s/test_provider_contract_kind.py` and `tests/k8s/conftest.py` (kind fixtures): define the scenarios once (create accepted, 409 adopt, quota 403, PSA 403, missing RuntimeClass, Pending Unschedulable, delete with delay, exec stream close, 404 on a gone pod) and run each against `FakeK8sApi` and against the kind API server through `K8sApiPort`
    - The kind fixtures create the `labs` namespace with the restricted PSA labels, so the test needs only the kind cluster of task 6.1 and not the Chart
    - Both runs must give the same classification, exception type and final pod set. Fix any drift in the fake
    - _Requirements: 1.1, 1.5, 1.9, 2.11, 2.12, 22.1_

  - [x] 6.23 Run the shared provider suites against kind
    - `tests/k8s/test_provider_suites_kind.py`: run the Sandbox_Port contract suite and the labs isolation, grading isolation, tamper-resistance and check-abuse suites with `KubernetesSandboxProvider` on the kind API server, with zero skips and zero exclusions beyond those of the Docker_Provider run
    - _Requirements: 1.9, 5.7, 16.10_

- [x] 7. Checkpoint - walking skeleton green (review point, gates no other task)
  - Confirm that the layout decision is recorded in ADR 0009, the kind install succeeds, `helm test` passes, one Lab_Pod is provisioned and destroyed, the real-API contract test shows the fake and kind agree, and the shared provider suites pass on kind
  - Ensure all tests pass, ask the user if questions arise
  - Reopened by its own findings: the production K8sApiPort client did not exist and the worker image could not sync content, so the kind chart install, `helm test` and the smoke test could not be verified; 7.1 to 7.3 close those gaps before this checkpoint is accepted

  - [x] 7.1 Add the production K8sApiPort client
    - Write `edu_it_infrastructure.sandbox.k8s_client` with an exact pinned `kubernetes` dependency: create, read, delete, list and label pods, exec and terminal streaming, errors mapped like the fake and `KubectlK8sApi`
    - In-cluster auth through a projected ServiceAccount token volume on Api and Worker (the Chart disables automount)
    - _Requirements: 1.1, 1.4, 1.5, 6.3_

  - [x] 7.2 Fix the worker image so content sync finds the lab images
    - Copy `lab-images/` into the worker image so content sync finds `<repo>/lab-images/<name>`
    - _Requirements: 16.1_

  - [x] 7.3 Re-verify on kind what the checkpoint could not
    - Run the real-API contract scenarios against the new client, then the kind chart install, `helm test` and `tests/k8s/test_smoke_kind.py`
    - _Requirements: 1.4, 1.5, 6.3, 16.1_

- [x] 8. Application and worker behavior
  - [x] 8.1 Implement secret handling
    - `SecretSource` over mounted files, `SecretHolder` (keeps the last good value, never raises on refresh) and `check_required_secrets`
    - Log one record per missing key name only, never a value
    - _Requirements: 23.2, 23.6_

  - [x] 8.2 Write property test for startup secret check
    - **Property 22: Startup secret check**
    - **Validates: Requirements 23.2**

  - [x] 8.3 Write property test for secret refresh
    - **Property 23: Secret refresh keeps the last good value**
    - **Validates: Requirements 23.6**

  - [x] 8.4 Add capacity handling to the provisioning use case
    - Catch `ProvisionCapacityError` before `ProvisionError`, apply `capacity_decision`, requeue or fail with reason `capacity`
    - Do not count the failure against the retry allowance, record an attempt of kind `capacity`, increment the quota rejection metric once per rejection
    - Add FR and EN message keys `labs.capacity`, `labs.session_interrupted` and the provisioning failure message, with no raw provider text
    - _Requirements: 6.8, 6.9, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7_

  - [x] 8.5 Write property test for retry accounting
    - **Property 14: Capacity failures do not consume the retry allowance**
    - **Validates: Requirements 22.4, 22.5**

  - [x] 8.6 Write property test for learner messages
    - **Property 5: Learner messages are translated and carry no raw detail**
    - **Validates: Requirements 6.8, 19.1, 22.2**

  - [x] 8.7 Implement infrastructure loss handling and the reaper extension
    - `end_by_infrastructure` use case, `watch_lab_pods` job with reconnect backoff, evicted pod removal, reaper using `select_reaper_actions`
    - Increment the infrastructure loss metric once per session
    - _Requirements: 6.6, 6.7, 19.2, 19.3, 19.4, 19.5, 19.7, 19.8, 20.3, 20.7_

  - [x] 8.8 Add Worker startup and shutdown behavior
    - Call `parse_sandbox_backend`, `runtime_guard` (warn once or refuse and exit non-zero) and `check_required_secrets` before consuming a job
    - Register `watch_lab_pods`
    - Handle SIGTERM: stop consuming, finish or requeue each in-flight job with the attempt count unchanged at `grace - 5 s`, then exit
    - Mark interrupted grading runs failed on restart without re-running them
    - _Requirements: 1.3, 2.9, 2.10, 14.10, 14.11, 20.5, 20.6, 20.7, 23.2_

  - [x] 8.9 Write property test for worker shutdown
    - **Property 18: Worker shutdown requeues unfinished jobs**
    - **Validates: Requirements 14.10, 14.11**

  - [x] 8.10 Write unit tests for worker restart and infrastructure loss
    - Provisioning rerun after crash, grading run interrupted, reaper resume, evicted pod removal, learner progress unchanged
    - _Requirements: 19.2, 19.4, 20.5, 20.6, 20.7_

- [x] 9. Api and web behavior
  - [x] 9.1 Implement `DrainCoordinator` and the admin endpoint
    - Idempotent `start_drain()`: readiness returns 503 first, wait the preStop delay, reject new terminal upgrades, send the reconnect message, close sockets, force-close at the drain period (5 to 300 s, default 30), leave Lab_Pods untouched
    - `/internal/drain` on the admin port for the `preStop` hook and a SIGTERM handler that calls the same method
    - _Requirements: 14.1, 14.2, 14.3, 14.9, 14.12_

  - [x] 9.2 Write property test for drain
    - **Property 16: Drain closes or force-closes every socket**
    - **Validates: Requirements 14.1, 14.2, 14.3**

  - [x] 9.3 Extend the terminal gateway
    - Close each affected socket within 10 s of node loss with the translated `labs.session_interrupted` message and a reconnect indication, with no infrastructure detail
    - Re-verify ownership and expiry on every reconnect and refuse without attaching on failure
    - _Requirements: 14.7, 14.8, 19.1, 20.1, 20.4_

  - [x] 9.4 Add request id propagation
    - Middleware reads or generates `X-Request-ID` (UUID4), binds it to logs and the trace context, writes it into the job envelope
    - The Worker reads it from the envelope and binds it before running the handler
    - _Requirements: 13.8, 13.9, 13.10_

  - [x] 9.5 Write property test for request id propagation
    - **Property 19: Request id propagation**
    - **Validates: Requirements 13.8, 13.9, 13.10**

  - [x] 9.6 Implement the web reconnect client
    - TypeScript port of the `drain` and `restart` schedules tested against the shared JSON vectors
    - Show the reconnect indication within 1 s, translated FR/EN error with a manual retry action when the schedule ends, and a re-authentication error on ownership failure
    - _Requirements: 14.5, 14.6, 14.8, 20.1, 20.2_

  - [x] 9.7 Write unit tests for the gateway and the web client
    - Node loss close timing, ownership failure, schedule exhaustion, translated messages
    - _Requirements: 14.6, 14.8, 19.1, 20.1, 20.2, 20.4_

- [x] 10. Observability
  - [x] 10.1 Implement JSON logging with redaction
    - One JSON object per line, `request_id` on request-scoped records, `lab_session_id` on lab-scoped records, filter for secrets, terminal data and learner personal data
    - _Requirements: 13.8, 13.11_

  - [x] 10.2 Implement Prometheus metrics
    - Lab_Metrics from the design with the closed label set (`reason`, `result`, `provider`) and a Prometheus implementation of `LabMetrics`
    - _Requirements: 13.3, 13.4, 13.12_

  - [x] 10.3 Implement tracing with a bounded exporter
    - OTLP exporter only when the endpoint is set, background batch processing, at most 3 export attempts then drop
    - _Requirements: 13.1, 13.2_

  - [x] 10.4 Write property test for logs and metric labels
    - **Property 20: Logs and metrics exclude personal data**
    - **Validates: Requirements 13.8, 13.11, 13.12**

  - [x] 10.5 Write property test for bounded telemetry export
    - **Property 21: Telemetry export is bounded**
    - **Validates: Requirements 13.2**

  - [x] 10.6 Wire telemetry into the Api and the Worker
    - Dedicated metrics port separate from the traffic port, logging and tracing setup, Prometheus `LabMetrics` injected into the use cases
    - _Requirements: 13.1, 13.3, 13.4, 13.8_

- [x] 11. Checkpoint - application, Api, web and observability (review point, gates no other task)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11.1 Fix test-order pollution in the Kubernetes provider tests
  - In one combined run of `apps/api apps/worker packages tests/chart tests/shared` with `-m "not kind"`, 7 tests fail that pass in smaller groups: 2 in `test_k8s_exec.py`, 4 in `test_k8s_exec_timeout_and_terminal.py` and `test_k8s_provider_lifecycle.py::test_raw_api_text_is_logged_not_raised`
  - Isolate the order dependency (run with `-p no:randomly`, bisect the `apps` and `packages/infrastructure` order), suspect logging or caplog state leaking between tests
  - Fix the leaking fixture or logger state so the combined run reports zero failures, without removing or skipping any test
  - _Requirements: 26.9_

- [x] 12. Full Helm chart
  - [x] 12.1 Complete the Api, Worker and web workloads
    - Add the web Deployment and Service (OQ-8) and complete the Api and Worker Deployments: liveness, readiness and startup probes with the value bounds, resources with limit at least request, security contexts, topology spread (`maxSkew: 1`) for the Api, rolling update with `maxUnavailable: 0` and `maxSurge: 1`, default 2 Api replicas, `priorityClassName` from task 6.9 on Api, Worker and web, optional secret volumes, `preStop` drain hook on the admin port, `terminationGracePeriodSeconds`, metrics port
    - Set `automountServiceAccountToken: true` in the pod spec only for the Api and the Worker
    - _Requirements: 8.1, 8.5, 8.6, 8.7, 8.12, 14.4, 14.12, 23.1, 23.4, 25.6_

  - [x] 12.2 Add the HorizontalPodAutoscalers and PodDisruptionBudgets
    - `hpa.yaml` for Api and Worker (CPU, or queue depth when `metricsAdapter.enabled`), `pdb.yaml` for Api and Worker, and the PodDisruptionBudget with `maxUnavailable: 0` for lab pods when `labs.drainProtection.enabled` (OQ-3)
    - _Requirements: 8.2, 8.4, 8.10, 8.11, 19.6, 20.8_

  - [x] 12.3 Add first-install hook ordering for the migration (risk R7)
    - Render the Migration_Job ServiceAccount, the database Secret or ExternalSecret and the non-secret ConfigMap as `pre-install,pre-upgrade` hooks with weight `-10` and `hook-delete-policy: before-hook-creation`, and the Job itself with weight `0`
    - Use `creationPolicy: Orphan` on the database ExternalSecret so the synced Secret survives hook cleanup
    - Keep the Job after failure so its log stays available, with `backoffLimit` and `activeDeadlineSeconds` from values
    - _Requirements: 8.8, 8.9, 21.1, 21.2, 21.4, 21.5_

  - [x] 12.4 Add the platform network policies with the API server egress
    - `networkpolicy-platform.yaml`: default-deny, PostgreSQL and Redis only for Api and Worker, Worker egress to the LLM endpoint, Kubernetes API and DNS only, Api egress to the Kubernetes API, PostgreSQL, Redis, object storage and DNS only, Migration_Job egress to PostgreSQL and DNS only, ingress to Api and web from the ingress controller only
    - Render the Kubernetes API egress as an `ipBlock` rule from `networkPolicy.kubernetesApi.cidrs`
    - Render the optional monitoring-namespace ingress on the metrics port only (OQ-4)
    - _Requirements: 10.10, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.13, 11.14_

  - [x] 12.5 Add ingress and Gateway API routes
    - `Ingress` for `ingress-nginx` with Upgrade and Connection handling and WebSocket timeouts, `Gateway` reference and `HTTPRoute` for `gateway-api`, TLS from a certificate reference, one host with `/fr` and `/en` to web and Api routes to the Api
    - _Requirements: 9.1, 9.2, 9.4, 9.6, 9.7, 9.8, 9.10_

  - [x] 12.6 Add secrets and per-Target configuration
    - One `ExternalSecret` per secret read, store name, kind and remote keys from values, plain Secrets only for the dev Target
    - Populate the per-Target values of the four Values_Files: LLM, object storage, workload identity annotations on the ServiceAccounts, node pool selectors, runtime class kind
    - _Requirements: 10.1, 10.2, 10.5, 10.7, 10.8, 10.9, 23.5_

  - [x] 12.7 Add monitoring and admission templates
    - `ServiceMonitor` or scrape annotations by `observability.scrapeMode`, collector endpoint setting, `PrometheusRule` for quota rejection and `ExternalSecret` alerts (only when enabled), and `image-policy.yaml` with the signature verification Kyverno `ClusterPolicy`. Kyverno is an operator-installed optional prerequisite that the Chart never installs; the policy renders only when `imagePolicy.enabled` is true (default false) and is scoped to `platform` and `labs`
    - _Requirements: 12.10, 13.1, 13.5, 13.6, 22.9, 23.5_

  - [x] 12.8 Implement the CNI check command and hook Job
    - `python -m edu_it_infrastructure.cni_check` creates the two probe pods and the policy, uses `cni_check_outcome`, exits non-zero on `Fail`, writes the warning on `Warn`
    - `job-cni-check.yaml` as a post-install hook with `activeDeadlineSeconds: 120` and `rbac-platform.yaml` with its ServiceAccount and Role in `platform`
    - _Requirements: 11.9, 11.10, 11.11, 11.12_

  - [x] 12.9 Add the LLM region helper
    - `check_llm_region` under `tools/` (EU list from ADR 0007, dev flag, rejection names the region)
    - _Requirements: 10.6_

  - [x] 12.10 Write property test for the LLM region check
    - **Property 27: LLM region check**
    - **Validates: Requirements 10.6**

  - [x] 12.11 Complete the cross-key validation rules
    - Add the `fail` helpers in `_helpers.tpl` and the schema constraints for: replicas, ingress mode, WebSocket timeouts at least the Session_TTL, certificate reference, termination grace (same rule as task 4.10), migration `backoffLimit` 0 to 6 and `activeDeadlineSeconds` 30 to 3600, ExternalSecret store name and remote keys, LLM region, scrape mode, probe bounds, image digest, `kindTest.sessionTtlSeconds` absent from the four Values_Files
    - Every error names the offending key or value and renders no manifests
    - _Requirements: 7.7, 7.8, 8.3, 8.8, 9.3, 9.5, 9.9, 10.4, 10.6, 11.14, 13.7, 14.4, 21.4_

  - [x] 12.12 Write the chart README and NOTES.txt
    - Operator-managed namespace names and PSA labels, NetworkPolicy enforcement warning output
    - _Requirements: 7.10, 11.11_

  - [x] 12.13 Write rendered-manifest tests
    - For all four values files: kind allowlist, RBAC rules, policies including the API server `ipBlock`, probes, resources, security contexts, PriorityClass value and assignment, hook weights and `Orphan` policy of task 12.3, rendered `terminationGracePeriodSeconds` satisfying Property 34, ingress modes, quota and limit range keys, no secret findings, no cloud-specific kinds, `helm lint` with zero warnings and `kubeconform` validation
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 4.1, 4.2, 4.3, 4.6, 4.7, 7.2, 7.5, 7.6, 8.1, 8.5, 8.6, 8.12, 9.1, 9.2, 10.1, 10.3, 10.10, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.13_

  - [x] 12.14 Write invalid-values tests
    - Mutate one key at a time (missing key, wrong type, bad digest, replicas, ingress mode, timeouts, certificate, region, scrape mode, grace period, empty `networkPolicy.kubernetesApi.cidrs`, migration bounds 7, 29 and 3601, PriorityClass value not above lab pods, dev datastores in production) and assert that the error names the key and no manifest is rendered
    - _Requirements: 7.7, 7.12, 8.3, 8.8, 8.12, 9.3, 9.5, 9.9, 10.4, 10.6, 11.14, 13.7, 14.4, 21.4_

  - [x] 12.15 Finalize ADR 0010 and the deployment steering
    - Set `docs/adr/0010-helm-chart-conventions.md` to `Accepted` and align it with the shipped chart
    - Align `.kiro/steering/deployment.md` with the final rules in the same change
    - _Requirements: 7.2, 7.3, 7.4, 8.8, 11.4_

- [x] 13. Image supply chain
  - [x] 13.1 Extend the workflow with multi-arch build, scan, SBOM and image checks
    - Build every image for `linux/amd64` and `linux/arm64` and publish one multi-architecture manifest, failing the run and naming the image and platform when any build fails, with no publish from that run
    - Scan with the severity threshold from the pipeline configuration, fail on a finding with a fix at CRITICAL or HIGH and on an incomplete scan
    - Generate an SBOM per image and attach it by digest
    - Run `check_image_contents.py` and `check_image_refs.py` on every built image and on the chart values
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.11, 15.1, 15.2_

  - [x] 13.2 Add the signature verification tool
    - `tools/verify_image_signatures.py` that runs `cosign verify` for every image digest and fails on a missing or invalid signature
    - For pull-request runs it verifies against the local registry digests only and records a note that signature verification was skipped
    - _Requirements: 12.14_

  - [x] 13.3 Write unit tests for the verification tool
    - Valid signature, missing signature, invalid signature, pull-request mode note
    - _Requirements: 12.14_

  - [x] 13.4 Add keyless signing, provenance and gating to the workflow
    - Signing job with `id-token: write` set only on that job, keyless signing by digest, provenance attestation by digest, failure of SBOM attach, signing or signature publication marks the build as failed and the image as not released
    - Sign and push only on the default branch or a release tag, never on other branches and never for fork pull requests
    - Add the signature verification step (task 13.2) before every install in the kind job of task 6.14; task 15.1 reuses it before the Upgrade_Test
    - Pull-request and other branch runs verify the local-registry digests, record that signature verification was skipped, and neither sign nor push (Req 15.8)
    - _Requirements: 12.8, 12.9, 12.12, 12.13, 12.14, 15.8, 15.9_

  - [x] 13.5 Finalize ADR 0011 and the deployment steering
    - Set `docs/adr/0011-image-supply-chain.md` to `Accepted` and align it with the shipped pipeline
    - Align `.kiro/steering/deployment.md` with the final rules in the same change
    - _Requirements: 12.1, 12.3, 12.5, 12.7, 12.8, 12.10_

- [x] 14. Kind test suites and load test
  - [x] 14.1 Add the kind test values override
    - `deploy/helm/edu-it/ci/values-kind-test.yaml` with `kindTest.sessionTtlSeconds`, used only by the kind suites and never by the four production Values_Files
    - Make the harness pass it only when a test needs a short TTL
    - _Requirements: 16.4, 16.11_

  - [x] 14.2 Write the isolation suite `tests/k8s/test_isolation_kind.py`
    - First assert that a deny-all policy blocks a probe, then the Lab_Pod assertions (non-root, no token, no internet or other namespace within 10 s, deleted within 60 s of TTL), Worker Forbidden for one read and one create outside `labs`, Api Forbidden for pod create and delete, every unlisted Platform_Policies path blocked within 10 s, the three-container layout checks of requirement 16.10, and a fork-bomb PID limit case
    - Tag the tests with `# Claims:` ids
    - _Requirements: 2.2, 2.3, 3.6, 4.4, 4.5, 4.8, 11.7, 16.3, 16.4, 16.5, 16.7, 16.8, 16.10_

  - [x] 14.3 Write the E2E test `tests/k8s/test_e2e_kind.py`
    - Install with `values-dev.yaml` on an empty cluster (proving the hook order of task 12.3), wait up to 600 s, report non-ready workloads and keep the cluster on failure, run the Phase 3 end-to-end flow, then upgrade once to prove the hook resources are replaced cleanly
    - Tag the tests with `# Claims:` ids
    - _Requirements: 16.1, 16.2, 8.8, 8.9_

  - [x] 14.4 Write the Upgrade_Test `tests/k8s/test_upgrade_kind.py`
    - Resolve the previous release as the chart and image digests of the most recent release tag, or of the base commit when no tag exists (risk R9), install it, start 3 Lab_Sessions, upgrade to the candidate with one migration, and assert same pod identity, terminal command output, schema version, readiness and client reconnect within 30 s
    - Include the failed-migration variant (Lab_Sessions still running, old Deployments unchanged), the previous Api running against the candidate schema, and the signature verification of task 13.2
    - Put shared helpers in `tests/k8s/upgrade_fixtures.py`
    - Tag the tests with `# Claims:` ids
    - _Requirements: 21.1, 21.6, 21.8, 25.1, 25.2, 25.3, 25.4, 25.5, 25.7, 25.8, 12.14_

  - [x] 14.5 Write the helm rollback verification `tests/k8s/test_rollback_kind.py`
    - After the verified upgrade, run `helm rollback` to the baseline revision and assert that the release revision returned to the previous one, that the count of active Lab_Sessions equals the count recorded before the upgrade, and that each Lab_Session remains active and attachable
    - Repeat after a failed upgrade (failing migration) to prove the rollback path of the Deployment_Guide
    - Tag the tests with `# Claims:` ids
    - _Requirements: 21.7, 21.8, 25.9_

  - [x] 14.6 Write the failure scenario tests for node loss and pod restarts
    - Cordon, drain and delete a lab node (terminal close within 10 s, session ended-by-infrastructure within 30 s, slot released, evicted pod removed within 60 s, drain protection), kill an Api pod during an active terminal (with and without a drain), kill a Worker pod during provisioning and during grading, reaper resume within 60 s
    - Tag the tests with `# Claims:` ids
    - _Requirements: 19.1, 19.2, 19.3, 19.5, 19.6, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8_

  - [x] 14.7 Write the failure scenario tests for migration, quota and secrets
    - `helm upgrade` with a failing migration, fill the Labs quota and assert the capacity behavior (translated message, no partial pod, allowance unchanged, requeue then fail), install and upgrade with an unsyncable `ExternalSecret` (pod not Ready, exit within 30 s, Helm timeout, old pods still serving)
    - Tag the tests with `# Claims:` ids
    - _Requirements: 21.1, 21.2, 21.5, 22.1, 22.2, 22.3, 22.8, 23.1, 23.3, 23.4_

  - [x] 14.8 Implement the Load_Test `tools/perf/lab_load_k8s.py`
    - `nearest_rank_percentile` and `evaluate_targets`, 10 concurrent sessions held for 60 s, at least 20 readiness samples, grading latency, runner CPU and memory sampled every 5 s or less, image pull time reported separately from Sandbox_Ready_Time (risk R4), cleanup of all created pods also on failure or abort, report of started sessions when 10 cannot start
    - Write the results as JSON (including the measured per-session CPU, memory and storage) so the Deployment_Guide can use them
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 24.6, 24.7, 24.8, 24.9_

  - [x] 14.9 Write property test for nearest-rank percentile
    - **Property 28: Nearest-rank percentile**
    - **Validates: Requirements 24.1, 24.5**

  - [x] 14.10 Write property test for load test targets
    - **Property 29: Load test target evaluation**
    - **Validates: Requirements 24.6**

- [x] 15. Complete CI workflow
  - [x] 15.1 Extend the workflow with the kind install and test jobs
    - Add to `.github/workflows/deployment.yml`: kind install with a 600 s wait, skipping the E2E, isolation, upgrade, rollback, failure scenario and load tests when the install is not ready, then running them, the real-API contract test and the provider suites on kind, and `check_values_consistency`, and run the signature verification step of task 13.4 before the Upgrade_Test
    - _Requirements: 12.14, 15.3, 15.4, 15.5, 15.6, 16.1, 25.1_

  - [x] 15.2 Add artifacts and release gating
    - Upload step logs and the Kubernetes event dump on any failure with 14 days retention, upload the Load_Test results on every run or record that none were produced
    - Make the sign and push jobs depend on all verification jobs, run only on the default branch or release tags, and make branch and pull-request runs verify the local-registry digests, record that signature verification was skipped, and neither sign nor push (Req 15.8)
    - Run `check_ci_pins` as a required step
    - _Requirements: 15.7, 15.8, 15.9, 15.10, 15.11, 24.6_

- [x] 16. Documents
  - [x] 16.1 Write `docs/DEPLOYMENT.md` from measured data
    - Prerequisites per Target with minimum version or verification command, ordered install, upgrade and rollback steps with success checks, autoscaling table, Session_Cost taken from the measured Load_Test JSON of task 14.8 (state "not measured" when no result exists) with a worked sizing example, Go_Live_Checklist (one item each for the required checks, real-Target RuntimeClass, Runc_Warning removal, kubelet `podPidsLimit`), blocked go-live rule with corrective sections, CNI enforcement statement, finding `networkPolicy.kubernetesApi.cidrs`, failed-upgrade rollback and active-session check, `ExternalSecret` diagnosis, out-of-scope list
    - _Requirements: 11.8, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9, 17.10, 21.7, 23.7_

  - [x] 16.2 Rewrite `README.md`
    - English body with a purpose statement of at most 100 words, one French section of at most 300 words, quick start, and links to `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md` and every steering file
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.6_

  - [x] 16.3 Add `tools/check_docs.py`
    - Verify required headings and checklist items, link resolution (report broken links), and the French word count
    - _Requirements: 17.6, 17.8, 18.2, 18.5_

  - [x] 16.4 Write unit tests for the docs check
    - Broken link, missing checklist item, French section over 300 words
    - _Requirements: 17.8, 18.2, 18.5_

  - [x] 16.5 Run the docs check in CI
    - Add a docs check job to `.github/workflows/deployment.yml`
    - _Requirements: 18.5_

- [x] 17. Traceability
  - [x] 17.1 Close the traceability gaps
    - Run `tools/check_traceability.py`, add `# Claims:` tags or table entries for every criterion of Requirements 1 to 25 that no test claims yet (chart tests, document checks and CI checks included), and repeat until it exits 0
    - _Requirements: 26.1, 26.2_

- [x] 18. Checkpoint - full verification (review point, gates no other task)
  - Run the existing application test suites and confirm zero failures with no tests removed or skipped (Requirement 26.9)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 19. Write the Phase 5 report
  - [x] 19.1 Write `docs/reports/phase-5.md`
    - List per milestone each command run and its outcome (passed, failed, not run), the Load_Test results and Sandbox_Ready_Time p95 or "not run", the gVisor fallback to runc on kind, the chosen pod layout, and a Not verified section (real-Target RuntimeClass check and every unrun check, with reasons)
    - State a passing status only when every required check ran and passed
    - Include a next-steps list ordered by priority with rank numbers: 1 placement assessment, 2 project reviewers, 3 warm pool
    - Do not implement cluster provisioning, warm pool, multi-region or backups
    - _Requirements: 16.6, 26.3, 26.4, 26.5, 26.6, 26.7, 26.8, 26.10_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability, and each test task tags its tests with `# Claims:` ids so claims accumulate as tests land
- Checkpoints (7, 11, 18) are review points and not gates: they gate no other task and are not in the dependency graph. Each task waits only for its true data and file dependencies
- Only the pod builder and `as_user` table (4.3), the Property 2 test (4.4) and the provider tasks (5.2 to 5.10) wait for the layout decision of task 1.2. Backend parsing (4.1), the classifier (4.6) and `FakeK8sApi` (5.1) do not
- Domain foundations, migrations, observability, the chart skeleton, the Dockerfiles, the kind harness and the CI skeleton start in the first waves, in parallel with the spike
- Tasks that write the same file are in different waves: ADR 0009 (1.1, 1.2, 5.10), `deployment.md` (1.1, 12.15, 13.5), `.github/workflows/deployment.yml` (6.21, 13.1, 6.14, 13.4, 15.1, 15.2, 16.5), the Values_Files and schema (6.7, 6.8, 6.9, 12.6, 12.11), `_helpers.tpl` (6.7, 6.8, 6.9, 12.11), `serviceaccounts.yaml` (6.10, 12.3, 12.6), the workload templates (6.13, 12.1, 12.3), `tools/kind_harness.py` (6.1, 14.1) and `domain/config.py` (2.1, 4.3)
- Kind-marked tests need Docker, kind and helm and are run by the CI kind job; unit and property tests run without a cluster
- Property tests validate universal correctness properties, unit tests validate specific examples and edge cases
## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1", "3.3", "4.1", "4.10", "4.12", "6.1", "6.2", "6.3", "6.4", "6.15", "6.17", "6.19", "8.1", "9.4", "10.1", "10.3", "12.9", "13.2", "16.3"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.2", "3.4", "4.2", "4.6", "4.11", "4.13", "5.1", "6.5", "6.6", "6.7", "6.16", "6.18", "6.20", "6.21", "8.2", "8.3", "9.1", "9.5", "10.2", "10.5", "12.10", "13.3", "16.4"] },
    { "id": 2, "tasks": ["2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "4.3", "4.7", "4.8", "6.8", "6.10", "6.11", "6.12", "8.4", "8.7", "9.2", "9.6", "10.4", "12.2", "12.5", "12.7", "12.8", "13.1", "14.1"] },
    { "id": 3, "tasks": ["4.4", "4.5", "4.9", "5.2", "6.9", "6.13", "8.5", "8.8", "9.3", "12.4", "12.12"] },
    { "id": 4, "tasks": ["5.3", "5.5", "5.6", "5.7", "5.8", "8.6", "8.9", "8.10", "9.7", "10.6", "12.1"] },
    { "id": 5, "tasks": ["5.4", "5.9", "6.22", "12.3"] },
    { "id": 6, "tasks": ["5.10", "6.14", "6.23", "12.6", "14.8"] },
    { "id": 7, "tasks": ["12.11", "13.4", "14.2", "14.9", "14.10"] },
    { "id": 8, "tasks": ["12.13", "12.14", "12.15", "14.3", "14.4", "14.6", "14.7"] },
    { "id": 9, "tasks": ["13.5", "14.5"] },
    { "id": 10, "tasks": ["15.1", "16.1"] },
    { "id": 11, "tasks": ["15.2", "16.2"] },
    { "id": 12, "tasks": ["16.5"] },
    { "id": 13, "tasks": ["17.1"] },
    { "id": 14, "tasks": ["19.1"] }
  ]
}
```
