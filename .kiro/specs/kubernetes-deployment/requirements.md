# Requirements Document

## Introduction

This spec makes EDU-IT production-ready on Kubernetes. The platform must run unchanged on EKS, AKS and on-premise, using standard Kubernetes APIs only, with every provider difference isolated in Helm values (see #[[file:.kiro/steering/deployment.md]] and `docs/DEPLOYMENT-DESIGN.md`).

The work is split into three milestones:

- **Milestone A: Kubernetes sandbox provider.** A `SandboxProvider` implementation for Kubernetes (prod) that applies every Kubernetes control from #[[file:.kiro/steering/labs.md]] and keeps the grading isolation and session limits of the Docker provider delivered in the hands-on-labs spec.
- **Milestone B: Helm and images.** The chart `deploy/helm/edu-it`, platform namespace NetworkPolicies, and production container images.
- **Milestone C: operations and CI.** Observability, graceful shutdown, the CI pipeline, `docs/DEPLOYMENT.md`, the final README, and the Phase 5 report.

Out of scope: cluster provisioning, warm pool implementation (the extension point is kept), multi-region, and backups.

Existing business numbers (Session_TTL 60 minutes, Idle_Timeout 15 minutes, grading interval 10 s, grading deadline 15 s, provisioning retries and rate) live in `domain/config.py` and are reused unchanged. New business numbers introduced here are added to `domain/config.py` or to Helm values, as stated in each requirement.

## Glossary

- **EDU-IT**: The bilingual (FR/EN) re-learning platform for IT professionals.
- **Kubernetes_Provider**: The `SandboxProvider` implementation for Kubernetes (prod) that provisions one pod per Lab_Session in the Labs_Namespace.
- **Docker_Provider**: The existing dev-only `SandboxProvider` implementation, used as the behavioral reference for grading isolation and session limits.
- **Sandbox_Port**: The `SandboxProvider` interface defined in the domain (`provision`, `exec`, `attach_terminal`, `copy_in`, `copy_out`, `destroy`, `health`, `list_sandboxes`).
- **Lab_Session**: A running lab instance for one learner, backed by one Lab_Pod.
- **Lab_Pod**: The single pod created for one Lab_Session in the Labs_Namespace.
- **Labs_Namespace**: The `labs` Kubernetes namespace, labelled for the restricted Pod Security Admission profile.
- **Platform_Namespace**: The `platform` Kubernetes namespace holding the web Deployment and Service, the api Deployment, the worker Deployment and the Migration_Job.
- **Web**: The `apps/web` Next.js Deployment and Service in the Platform_Namespace.
- **Monitoring_Namespace**: The namespace of the metrics scraper, selected by a Values_File key.
- **Kubernetes_API_Endpoint**: The Kubernetes API server destination (CIDR list or endpoint) taken from a Values_File key, because pod selectors cannot match the API server.
- **Image_Policy_Engine**: Kyverno, the cluster-scoped admission policy engine whose `ClusterPolicy` with `verifyImages` enforces image signatures. The operator installs Kyverno as an optional prerequisite; the Chart does not install it.
- **Pod_Layout**: The container and process-namespace arrangement of a Lab_Pod, chosen by the day-one spike on the Kind_Cluster and recorded in `docs/adr/0009`. The Pod_Layout lets learner processes be frozen with all capabilities dropped and gives no cross-container process visibility.
- **Platform_Priority_Class**: The `PriorityClass` that the Chart assigns to the Api, Worker and Web, with a value higher than the priority of Lab_Pods.
- **Api**: The `apps/api` FastAPI Deployment, which also hosts the terminal WebSocket.
- **Worker**: The `apps/worker` task-queue consumer Deployment.
- **Migration_Job**: The Helm pre-upgrade Job that applies database schema migrations.
- **Chart**: The Helm chart at `deploy/helm/edu-it`.
- **Values_File**: One of the four Helm values files of the Chart: `values-dev.yaml` (kind), `values-eks.yaml`, `values-aks.yaml`, `values-onprem.yaml`.
- **Target**: One of kind (dev), EKS, AKS or on-premise.
- **Lab_Node_Pool**: The dedicated node pool for Lab_Pods, with a taint that only Lab_Pods tolerate.
- **Lab_RuntimeClass**: The configurable `RuntimeClass` used by Lab_Pods (gVisor or Kata; plain runc is dev-only).
- **Runc_Warning**: The startup warning emitted when the Lab_RuntimeClass resolves to plain runc.
- **Labs_Allowlist**: The set of NetworkPolicies in the Labs_Namespace that permit only labeled flows (worker provisioning and exec, api terminal attach) on top of default-deny.
- **Platform_Policies**: The NetworkPolicies in the Platform_Namespace (default-deny plus labeled allowlists).
- **External_Secrets**: The External Secrets Operator resources (`ExternalSecret`, `SecretStore`) that sync secrets from the Target's secret manager into Kubernetes Secrets.
- **Terminal_Drain**: The graceful shutdown procedure of the Api that sends a reconnect message on each open terminal WebSocket and closes it.
- **Reaper**: The Worker job that destroys expired and orphaned Lab_Pods.
- **Lab_Metrics**: Prometheus metrics for labs: active sessions, provisioning duration, provisioning failures, grading duration, reaper deletions and quota rejections.
- **Image_Set**: The production container images for web, api, worker and migrations (where migrations use a separate image), plus the lab images.
- **SBOM**: A software bill of materials generated for each image.
- **CI_Pipeline**: The GitHub Actions workflows under `.github/workflows`.
- **Kind_Cluster**: A kind (Kubernetes in Docker) cluster created in CI or on a developer machine.
- **E2E_Test**: The Phase 3 end-to-end test, run against the Kind_Cluster.
- **Upgrade_Test**: The test that upgrades a running release from the previous release to the candidate release, including a migration, while Lab_Sessions are active.
- **Load_Test**: The test that runs concurrent Lab_Sessions and measures sandbox readiness, terminal and grading latency.
- **Sandbox_Ready_Time**: The time from the provisioning request to the Lab_Pod reporting Ready.
- **Deployment_Guide**: The document `docs/DEPLOYMENT.md`.
- **Go_Live_Checklist**: The security checklist section in the Deployment_Guide.
- **Phase_5_Report**: The document `docs/reports/phase-5.md`.
- **Session_Cost**: The CPU, memory, ephemeral storage and PID limits that one Lab_Session consumes, used for sizing.

## Requirements


### Requirement 1: Kubernetes provider conforms to the Sandbox_Port

**User Story:** As a platform engineer, I want a Kubernetes implementation of the Sandbox_Port, so that production labs run on Kubernetes without changing domain or application code.

#### Acceptance Criteria

1. THE Kubernetes_Provider SHALL implement every function of the Sandbox_Port with the same function names, parameter lists and result types as the Docker_Provider.
2. WHEN the sandbox provider setting is `kubernetes`, THE Api and THE Worker SHALL select the Kubernetes_Provider at startup.
3. IF the sandbox provider setting is not one of the supported values, THEN THE Api and THE Worker SHALL fail startup with an error indicating the invalid setting and the supported values, and SHALL NOT start serving requests.
4. WHEN a Lab_Session is created, THE Kubernetes_Provider SHALL create exactly one Lab_Pod for that Lab_Session.
5. IF the Lab_Pod creation request is rejected or the Lab_Pod does not reach the running state within 60 seconds, THEN THE Kubernetes_Provider SHALL delete any Lab_Pod it created for that Lab_Session, SHALL raise a sandbox provisioning error to the caller, and SHALL leave zero Lab_Pods for that Lab_Session.
6. WHEN a Lab_Pod is created, THE Kubernetes_Provider SHALL label it with the managed label and the Lab_Session identifier.
7. WHEN `list_sandboxes` is called, THE Kubernetes_Provider SHALL return only the Lab_Pods that carry the managed label, and SHALL return an empty list when no Lab_Pod carries the managed label.
8. THE Kubernetes_Provider SHALL use only standard Kubernetes APIs.
9. THE Kubernetes_Provider SHALL pass the same Sandbox_Port contract test suite as the Fake and the Docker_Provider, with zero failing and zero skipped contract tests.
10. WHEN `attach_terminal` is called for an existing Lab_Pod, THE Kubernetes_Provider SHALL return a `TerminalChannel` that supports `read`, `write` and `resize`.
11. IF `attach_terminal` is called for a Lab_Pod that does not exist, THEN THE Kubernetes_Provider SHALL raise `SandboxGone`.
12. IF the Lab_Pod no longer exists, THEN THE Kubernetes_Provider SHALL raise `SandboxGone` from `read`, `write` and `resize`.

### Requirement 2: Pod security and isolation controls

**User Story:** As a security engineer, I want every Kubernetes control from the labs steering applied to Lab_Pods, so that learner-controlled code cannot escape its session.

#### Acceptance Criteria

1. THE Labs_Namespace SHALL carry the Pod Security Admission labels that set the enforce level to the restricted profile.
2. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set on every container of the Lab_Pod `runAsNonRoot` to true, a user ID greater than 0, `allowPrivilegeEscalation` to false, all capabilities dropped and the `RuntimeDefault` seccomp profile.
3. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set `automountServiceAccountToken` to false on the Lab_Pod.
4. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL leave `hostNetwork`, `hostPID` and `hostIPC` unset or false and SHALL define zero `hostPath` volumes on the Lab_Pod.
5. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set `activeDeadlineSeconds` on the Lab_Pod to a value equal to the Session_TTL in seconds.
6. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set CPU, memory and ephemeral-storage limits on the Lab_Pod, each equal to the corresponding limit that the Docker_Provider applies to the same SandboxSpec, and SHALL express the PID limit as a Lab_Pod annotation carrying the Docker_Provider PID limit value, enforced by the `podPidsLimit` kubelet setting of the Lab_Node_Pool and verified by a fork-bomb case in the isolation test suite.
7. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set the runtime class of the Lab_Pod to the Lab_RuntimeClass named in the Values_File.
8. WHEN the Kubernetes_Provider creates a Lab_Pod, THE Kubernetes_Provider SHALL set on the Lab_Pod the Lab_Node_Pool node selector and the Lab_Node_Pool toleration defined in the Values_File.
9. WHERE the Lab_RuntimeClass resolves to plain runc and the deployment environment is not production, THE Worker SHALL emit the Runc_Warning once at startup, before it consumes its first job.
10. WHERE the deployment environment is production and the Lab_RuntimeClass resolves to plain runc, THE Worker SHALL refuse to start, log the reason, and exit with a non-zero status without consuming any job.
11. IF the Labs_Namespace admission rejects a Lab_Pod for a Pod Security violation, THEN THE Kubernetes_Provider SHALL report a provisioning failure to the caller, log the violation detail, and leave no Lab_Pod for that session in the Labs_Namespace.
12. IF the Lab_RuntimeClass named in the Values_File does not exist in the cluster when the Kubernetes_Provider creates a Lab_Pod, THEN THE Kubernetes_Provider SHALL report a provisioning failure to the caller and log the missing RuntimeClass name, without falling back to another runtime.

### Requirement 3: Labs namespace network isolation and quotas

**User Story:** As a security engineer, I want default-deny networking and resource ceilings in the Labs_Namespace, so that a Lab_Pod cannot reach the internet or other namespaces and cannot exhaust the cluster.

#### Acceptance Criteria

1. THE Chart SHALL render a default-deny NetworkPolicy in the Labs_Namespace that selects all pods in the namespace and denies all ingress and all egress, with no allow rules other than those rendered by criteria 2 and 7.
2. THE Chart SHALL render the Labs_Allowlist as labeled NetworkPolicy rules that permit ingress to Lab_Pods only from Api pods for terminal attach, and only from Worker pods for exec, copy and health operations, and that permit no other source.
3. THE Chart SHALL render a `ResourceQuota` in the Labs_Namespace that sets a numeric cap for each of: the number of pods, total CPU requests, total CPU limits, total memory requests, total memory limits and total ephemeral storage.
4. THE Chart SHALL render a `LimitRange` in the Labs_Namespace that sets, for containers, a default request, a default limit, and a maximum for each of CPU, memory and ephemeral storage.
5. THE Chart SHALL expose every `ResourceQuota` cap and every `LimitRange` value as a key in each Values_File, so that rendering with a changed value changes the rendered manifest.
6. WHEN a Lab_Pod runs, THE Labs_Namespace network policy SHALL drop 100% of connection attempts from the Lab_Pod to internet addresses, to pods in other namespaces and to other Lab_Pods in the Labs_Namespace.
7. WHERE a Values_File sets the Lab_Pod DNS egress option to enabled, THE Chart SHALL render an egress rule permitting Lab_Pods to reach the cluster DNS service on port 53 (UDP and TCP) only, and THE Chart SHALL render no DNS egress rule when the option is disabled, with the option set to disabled by default.
8. IF a Lab_Pod creation would exceed a `ResourceQuota` cap, THEN THE Labs_Namespace SHALL reject the creation request, leave existing Lab_Pods running and unchanged, and return a quota-exceeded error to the caller.

### Requirement 4: Worker RBAC limited to the labs namespace

**User Story:** As a security engineer, I want the Worker to hold only the permissions it needs in the Labs_Namespace, so that a compromised Worker cannot act on other resources.

#### Acceptance Criteria

1. THE Chart SHALL render exactly one namespaced `Role` for the Worker in the Labs_Namespace, whose rules grant only the `create`, `get`, `list`, `watch` and `delete` verbs on `pods` and only the `create` and `get` verbs on `pods/exec`, with no wildcard (`*`) verb, resource or apiGroup, no `resourceNames` widening, and no other verb or resource.
2. THE Chart SHALL bind the Role to the Worker ServiceAccount with exactly one `RoleBinding` in the Labs_Namespace whose only subject is the Worker ServiceAccount.
3. THE Chart SHALL render zero `ClusterRole` and zero `ClusterRoleBinding` objects that reference the Worker ServiceAccount, and zero `RoleBinding` objects outside the Labs_Namespace that reference the Worker ServiceAccount.
4. IF the Worker attempts to read, create, update or delete a resource in any namespace other than the Labs_Namespace, or a cluster-scoped resource, THEN THE Kubernetes API SHALL deny the request with a Forbidden response and the Worker SHALL receive no resource data.
5. IF the Worker attempts to create, update or delete a resource other than a pod in the Labs_Namespace, or attempts a verb on `pods` or `pods/exec` that is not listed in criterion 1, THEN THE Kubernetes API SHALL deny the request with a Forbidden response.
6. THE Chart SHALL render exactly one separate ServiceAccount for each of the Api, the Worker and the Migration_Job, and SHALL set `automountServiceAccountToken` to `false` on each ServiceAccount and on each pod spec of a workload that does not call the Kubernetes API.
7. THE Chart SHALL grant the Api no `create`, `update`, `patch`, `delete` or `deletecollection` verb on `pods` in any namespace, and SHALL grant the Api, through one namespaced `Role` in the Labs_Namespace only, the `get` verb on `pods` and the `create` verb on `pods/exec`, because terminals attach through `pods/exec`.
8. IF the Api attempts to create or delete a pod in any namespace, THEN THE Kubernetes API SHALL deny the request with a Forbidden response.

### Requirement 5: Same grading isolation and session limits as the Docker_Provider

**User Story:** As a learner, I want grading to behave the same in production as in development, so that results are fair and bounded.

#### Acceptance Criteria

1. WHEN grading starts, THE Kubernetes_Provider SHALL copy the grading bundle into the Lab_Pod from a read-only source, owned by an identity that the learner processes cannot write as, before any Check runs.
2. WHEN the Grading_Run ends, whether it passed, failed, timed out or was aborted, THE Kubernetes_Provider SHALL remove the grading bundle from the Lab_Pod.
3. IF the grading bundle cannot be copied into the Lab_Pod or cannot be removed after the Grading_Run, THEN THE Worker SHALL fail the Grading_Run closed, record the `lab_run` with a failed status and return translated "could not complete" feedback to the learner.
4. WHEN grading starts, THE Worker SHALL freeze all learner processes in the Lab_Pod, through the existing grading use case calling the Sandbox_Port `exec` function, before any Check runs, completing within the freeze bound in `domain/config.py` and using at most the freeze attempt count in `domain/config.py`.
5. IF the freeze cannot be verified within the freeze bound and attempt count in `domain/config.py`, THEN THE Worker SHALL fail the Grading_Run closed without running any Check, record the `lab_run` with a failed status and return translated "could not complete" feedback to the learner.
6. WHEN `exec` is called with `timeout_s` and the command runs longer than `timeout_s` seconds, THE Kubernetes_Provider SHALL kill the command and every process in its process group and SHALL report `timed_out`.
7. THE Kubernetes_Provider SHALL pass every test in the labs isolation, grading isolation, tamper-resistance and check-abuse suites that the Docker_Provider passes, with the Kubernetes_Provider substituted and with zero additional skipped or excluded tests.
8. THE Worker SHALL enforce at most one Active_Session per learner, regardless of the provider.
9. IF a grading request arrives for a Lab_Session less than 10 s after that Lab_Session's previous grading request, THEN THE Worker SHALL reject the request without running any Check, SHALL leave the earlier Grading_Run unaffected and SHALL return translated feedback indicating how many seconds remain before the next request is allowed.
10. THE Worker SHALL enforce the provisioning rate limit in `domain/config.py`, regardless of the provider.
11. IF a grading check does not return within the 15 s target, THEN THE Worker SHALL return the translated "could not complete" feedback without raw check output and record the `lab_run` with a failed status.

### Requirement 6: Sandbox provisioning and cleanup

**User Story:** As a learner, I want my lab to be ready quickly and cleaned up after use, so that I can start working and leave no residue.

#### Acceptance Criteria

1. WHEN a provisioning request is received, THE Kubernetes_Provider SHALL create the Lab_Pod and return a `SandboxHandle` once the Lab_Pod reports Ready.
2. IF the Lab_Pod does not report Ready within the provisioning timeout defined in `domain/config.py`, THEN THE Kubernetes_Provider SHALL delete the Lab_Pod, wait until the Lab_Pod is absent, and report a provisioning failure to the caller without returning a `SandboxHandle`.
3. WHEN `destroy` is called, THE Kubernetes_Provider SHALL delete the Lab_Pod and wait until the Lab_Pod is absent, up to the destroy timeout defined in `domain/config.py`.
4. IF the Lab_Pod is still present when the destroy timeout elapses, THEN THE Kubernetes_Provider SHALL report a destroy failure to the caller and leave the Lab_Pod marked for the Reaper to delete.
5. WHEN `destroy` is called for a Lab_Pod that is already absent, THE Kubernetes_Provider SHALL return success without reporting an error.
6. WHEN the Session_TTL defined in `domain/config.py` elapses for a Lab_Session, THE Reaper SHALL delete the Lab_Pod of that Lab_Session and mark the Lab_Session as expired.
7. WHEN the Reaper finds a managed Lab_Pod with no matching Lab_Session row, THE Reaper SHALL delete the Lab_Pod.
8. IF sandbox provisioning fails, THEN THE Worker SHALL show the learner a translated, actionable error in the learner's selected locale (FR or EN) without raw provider output, leave zero partial Lab_Pods running for that session, retry at most 2 times, and start at most 1 provisioning attempt per 10 s per session.
9. IF a provisioning request is received for a session within 10 s of that session's previous provisioning attempt, THEN THE Worker SHALL reject the request with a translated rate-limit error and SHALL NOT create a Lab_Pod.
10. THE Kubernetes_Provider SHALL support the warm-pool extension point while keeping the Sandbox_Port interface unchanged.

### Requirement 7: Helm chart structure and portability

**User Story:** As an operator, I want one Helm chart that renders on every Target, so that I change only values to move between EKS, AKS and on-premise.

#### Acceptance Criteria

1. THE Chart SHALL reside at `deploy/helm/edu-it` and SHALL provide exactly four Values_Files named `values-dev.yaml`, `values-eks.yaml`, `values-aks.yaml` and `values-onprem.yaml`.
2. THE Chart SHALL render only upstream Kubernetes API kinds and the External Secrets and Gateway API kinds named in this document, SHALL render the Image_Policy_Engine policy kind only WHERE `imagePolicy.enabled` is true, and SHALL render zero cloud-provider-specific custom resource kinds.
3. THE Chart SHALL contain every provider-specific setting in a Values_File.
4. IF a template contains a provider conditional, THEN THE Chart SHALL select that conditional only by a Values_File key and SHALL NOT select it by a Target name, a cloud API probe or a Kubernetes version string.
5. WHEN `helm lint` runs against the Chart with each of the four Values_Files, THE Chart SHALL complete with zero errors and zero warnings.
6. WHEN `helm template` runs against the Chart with each of the four Values_Files, THE Chart SHALL render manifests in which 100% of objects pass schema validation against the target Kubernetes version declared by the Chart.
7. IF a Values_File omits a required key or sets a key to a value of the wrong type, THEN THE Chart SHALL fail `helm lint` and `helm template` with an error that names the offending key and SHALL render no manifests.
8. THE Chart SHALL include a `values.schema.json` that declares every required key of the Chart.
9. WHERE the Values_File key that selects namespace management is set to create, THE Chart SHALL create the Platform_Namespace and the Labs_Namespace objects, each carrying the Pod Security Admission enforce, audit and warn labels.
10. WHERE the Values_File key that selects namespace management is set to operator-managed, THE Chart SHALL render no Namespace object and SHALL document in the Chart README the namespace names and the Pod Security Admission labels that the operator creates.
11. THE Chart SHALL set the image reference of every workload container, init container and job container from a Values_File key, in the form of a repository followed by a `sha256` image digest, and SHALL contain no mutable tag as the sole image identifier.
12. IF a workload image value lacks a `sha256` digest, THEN THE Chart SHALL fail `helm lint` and `helm template` with an error that names the workload and SHALL render no manifests.

### Requirement 8: Workload configuration, probes, scaling and availability

**User Story:** As an operator, I want probes, autoscaling and disruption budgets, so that the platform stays available during load and maintenance.

#### Acceptance Criteria

1. THE Chart SHALL render an Api Deployment, a Worker Deployment and a Web Deployment with its Service, each with a liveness probe, a readiness probe and a startup probe, where every probe defines a probe handler, a period of 1 to 60 seconds, a timeout of 1 to 30 seconds that is less than or equal to the period, and a failure threshold of 1 to 30, all set from values.
2. THE Chart SHALL render a HorizontalPodAutoscaler for the Api and for the Worker, with minimum replicas, maximum replicas and target metrics set from values, where minimum replicas is an integer from 1 to 100 and maximum replicas is an integer from the minimum replicas value to 100.
3. IF a Values_File sets maximum replicas below minimum replicas for the Api or the Worker, THEN THE Chart SHALL fail rendering with an error message identifying the workload and the invalid values, and SHALL produce no manifests.
4. THE Chart SHALL render a PodDisruptionBudget for the Api and for the Worker, each setting exactly one of minimum available or maximum unavailable from values, such that at least 1 replica remains available during a voluntary disruption when the HorizontalPodAutoscaler minimum replicas is 2 or more.
5. THE Chart SHALL set CPU and memory resource requests and limits on every container, including init containers and the Migration_Job container, with each limit greater than or equal to its request.
6. THE Chart SHALL set on every platform container: run as non-root, a read-only root filesystem, all capabilities dropped, privilege escalation disallowed, and the `RuntimeDefault` seccomp profile.
7. THE Chart SHALL render pod anti-affinity or topology spread constraints, keyed on the node hostname, so that Api replicas spread across nodes with a maximum skew of 1.
8. THE Chart SHALL render a Migration_Job as a Helm `pre-install` and `pre-upgrade` hook, with a backoff limit of 0 to 6 and an active deadline of 30 to 3600 seconds (default 600), both set from values.
9. IF the Migration_Job fails or exceeds its active deadline, THEN THE Chart SHALL cause the Helm install or upgrade to fail and SHALL leave the existing Api and Worker Deployments unchanged.
10. WHERE a Values_File enables an external metrics adapter, THE Chart SHALL render the Worker HorizontalPodAutoscaler scaling metric from queue depth, with the target queue depth per replica set from values.
11. WHILE a Values_File does not enable an external metrics adapter, THE Chart SHALL render the Worker HorizontalPodAutoscaler scaling metric from CPU utilization, with the target percentage set from values in the range 1 to 100.
12. THE Chart SHALL render a Platform_Priority_Class and assign it to the Api, the Worker and the Web, and THE Chart SHALL set the Platform_Priority_Class value higher than the priority of Lab_Pods, so that quota or node pressure evicts Lab_Pods before platform pods.

### Requirement 9: Ingress, Gateway API and WebSocket timeouts

**User Story:** As an operator, I want to choose ingress-nginx or Gateway API per Target, so that traffic and terminal WebSockets work with the cluster's edge.

#### Acceptance Criteria

1. WHERE a Values_File sets the ingress mode to `ingress-nginx`, THE Chart SHALL render an `Ingress` that routes to the Api Service and to the web Service, and SHALL NOT render a `Gateway` or `HTTPRoute`.
2. WHERE a Values_File sets the ingress mode to `gateway-api`, THE Chart SHALL render a `Gateway` reference and an `HTTPRoute` that routes to the Api Service and to the web Service, and SHALL NOT render an `Ingress`.
3. IF a Values_File sets the ingress mode to a value other than `ingress-nginx` or `gateway-api`, THEN THE Chart SHALL fail rendering with an error message naming the invalid value and the two allowed values, and SHALL produce no manifests.
4. THE Chart SHALL set the WebSocket read timeout and the WebSocket send timeout, in seconds, from values, with an initial value for each that is greater than or equal to the Session_TTL.
5. IF a Values_File sets a WebSocket read timeout or send timeout that is less than the Session_TTL, THEN THE Chart SHALL fail rendering with an error message naming the offending value and the Session_TTL, and SHALL produce no manifests.
6. WHERE the ingress mode is `ingress-nginx`, THE Chart SHALL configure the `Upgrade` and `Connection` header handling for the terminal WebSocket path on the rendered `Ingress`.
7. WHERE the ingress mode is `gateway-api`, THE Chart SHALL configure the rendered `HTTPRoute` so that a WebSocket upgrade request to the terminal WebSocket path is routed to the Api Service and the connection stays open for at least the configured timeouts.
8. THE Chart SHALL terminate TLS at the ingress using a certificate reference taken from values, and SHALL reference no certificate or private key material inline in rendered manifests.
9. IF the certificate reference is empty or missing in the Values_File, THEN THE Chart SHALL fail rendering with an error message indicating that a certificate reference is required, and SHALL produce no manifests.
10. THE Chart SHALL expose the `/fr` and `/en` web routes and the Api routes through a single host taken from values, such that `/fr` and `/en` requests reach the web Service and Api route requests reach the Api Service.

### Requirement 10: External Secrets and LLM provider settings

**User Story:** As an operator, I want secrets synced from each Target's secret manager and LLM settings per Target, so that no secret is stored in Git or in an image.

#### Acceptance Criteria

1. THE Chart SHALL render exactly one `ExternalSecret` for each distinct secret that the Api, the Worker or the Migration_Job reads, and SHALL render no `ExternalSecret` for a secret that none of the three reads.
2. THE Chart SHALL read the `SecretStore` name, the `SecretStore` kind and each remote secret key from values, so that a Values_File selects AWS Secrets Manager, Azure Key Vault or a self-managed store without any template change.
3. THE Chart SHALL contain zero secret values in any template or Values_File, and a secret scan of the rendered manifests and of every Values_File, excluding the dev Target plain Secret placeholders permitted by criterion 9, SHALL report zero findings.
4. IF a required `ExternalSecret` value (`SecretStore` name or remote secret key) is empty or missing in a Values_File and the Values_File does not set the dev Target, THEN THE Chart SHALL fail rendering with an error that names the missing value, and SHALL produce no manifests.
5. THE Chart SHALL set the LLM provider name, model, region and endpoint from values per Target, with EU data residency configured as in `docs/adr/0007-llm-provider-and-data-residency.md`.
6. IF a Values_File that does not set the dev Target configures an LLM region outside the EU regions allowed by `docs/adr/0007-llm-provider-and-data-residency.md`, THEN THE Chart SHALL fail rendering with an error that names the offending region, and SHALL produce no manifests.
7. THE Chart SHALL set the object storage type (S3 or Azure Blob), bucket or container name, and endpoint from values per Target.
8. THE Chart SHALL set workload identity annotations (IRSA, Azure Workload Identity or none) on the service accounts of the Api, the Worker and the Migration_Job from values per Target, and SHALL add no workload identity annotation when the value is none.
9. WHERE a Values_File sets the dev Target, THE Chart SHALL allow plain Kubernetes Secrets in place of External_Secrets, and SHALL render no `ExternalSecret` for any secret that a plain Kubernetes Secret replaces.
10. THE Chart SHALL grant LLM provider egress to the Worker only, so that the rendered NetworkPolicies contain zero LLM egress rules selecting the Api, the Web or any lab pod, keeping LLM provider calls out of the learner request path.

### Requirement 11: Platform namespace network policies

**User Story:** As a security engineer, I want the Platform_Namespace locked down, so that only the Api and Worker reach PostgreSQL and Redis and the Worker's egress is limited.

#### Acceptance Criteria

1. THE Chart SHALL render a default-deny NetworkPolicy in the Platform_Namespace that selects all Pods in the Platform_Namespace and denies 100% of ingress and 100% of egress traffic that is not allowed by another rule in Requirements 11.2 to 11.6.
2. THE Chart SHALL render allow rules that permit only Pods labeled as the Api and Pods labeled as the Worker to open connections to the PostgreSQL port and the Redis port, and that permit no other Pod in any namespace to reach those ports.
3. THE Chart SHALL render an egress rule that limits the Worker egress to the LLM provider endpoint, the Kubernetes API and DNS, and that permits no other egress destination for the Worker.
4. THE Chart SHALL render an ingress rule that permits only the ingress controller to reach the Api Service port, and that permits no other source and no other Api port, except that WHERE monitoring ingress is enabled in the Values_File (disabled by default), THE Chart SHALL also permit the Monitoring_Namespace to reach the Api metrics port only.
5. THE Chart SHALL render the Api egress rule to permit only the Kubernetes_API_Endpoint (through which terminals attach with `pods/exec`), the PostgreSQL port, the Redis port, the object storage endpoint and DNS, and to permit no other Api egress destination.
6. THE Chart SHALL render the Migration_Job egress rule to permit only the PostgreSQL port and DNS, and to permit no other Migration_Job egress destination.
7. WHEN a connection is attempted on a path not listed in Requirements 11.2 to 11.6, THE Platform_Policies SHALL block the connection, and the connection attempt SHALL fail without a completed handshake.
8. THE Deployment_Guide SHALL state that the Target's CNI must enforce NetworkPolicy, and SHALL state that a CNI that does not enforce NetworkPolicy leaves all rendered policies without effect.
9. WHEN the Chart is installed, THE Chart SHALL run a post-install check Job that reports whether the CNI enforces NetworkPolicy, and the Job SHALL complete within 120 seconds.
10. IF the post-install check Job reports that the CNI does not enforce NetworkPolicy and the Values_File sets enforcement as required, THEN THE Chart SHALL fail the install and report an error message indicating that NetworkPolicy enforcement is absent.
11. IF the post-install check Job reports that the CNI does not enforce NetworkPolicy and the Values_File does not set enforcement as required, THEN THE Chart SHALL complete the install and report a warning indicating that NetworkPolicy enforcement is absent.
12. IF the post-install check Job does not complete within 120 seconds, THEN THE Chart SHALL treat the result as enforcement absent and apply the behavior defined in Requirements 11.10 and 11.11.
13. THE Chart SHALL render the Worker and Api egress to the Kubernetes API from a Values_File key holding the Kubernetes_API_Endpoint (API server CIDR list or endpoint), because the destination is provider specific and pod selectors cannot match the API server.
14. IF the Kubernetes_API_Endpoint key is empty or missing and the Chart renders NetworkPolicy objects, THEN THE Chart SHALL fail rendering with an error that names the key, and SHALL produce no manifests.

### Requirement 12: Production images

**User Story:** As a security engineer, I want hardened, traceable images, so that I can trust what runs in production.

#### Acceptance Criteria

1. WHEN the CI_Pipeline runs a build, THE CI_Pipeline SHALL build each image of the Image_Set for both `linux/amd64` and `linux/arm64` and SHALL publish each image as a single multi-architecture manifest covering both platforms.
2. IF the build for either platform of any image in the Image_Set fails, THEN THE CI_Pipeline SHALL fail the build, SHALL identify the failing image and platform in the build output, and SHALL NOT publish any image from that run.
3. WHEN the CI_Pipeline builds an image of the Image_Set, THE CI_Pipeline SHALL produce a final stage from a minimal base that contains no shell package manager binary (for example apk, apt, apt-get, dnf, yum or pip) and no shell binary (for example sh, bash).
4. THE Image_Set SHALL run each container as a non-root user with a numeric user ID greater than 0 (zero) declared in the image configuration.
5. WHEN the CI_Pipeline builds an image of the Image_Set, THE CI_Pipeline SHALL scan the image for vulnerabilities and SHALL fail the build if the scan reports at least one vulnerability with severity at or above the severity threshold defined in the pipeline configuration.
6. IF the vulnerability scan cannot complete (scanner error, vulnerability database unavailable, or no result within the pipeline scan timeout), THEN THE CI_Pipeline SHALL fail the build, SHALL report the scan as incomplete, and SHALL NOT publish the image.
7. WHEN an image of the Image_Set passes the vulnerability scan, THE CI_Pipeline SHALL generate an SBOM for the image and SHALL attach the SBOM to the image in the registry so that it is retrievable by the image digest.
8. WHEN an image of the Image_Set passes the vulnerability scan, THE CI_Pipeline SHALL sign the image by digest using keyless signing and SHALL publish the signature to the registry alongside the image.
9. IF SBOM generation, SBOM attachment, signing or signature publication fails for any image, THEN THE CI_Pipeline SHALL fail the build and SHALL NOT mark the image as released.
10. WHERE `imagePolicy.enabled` is true in a Values_File, THE Chart SHALL render an Image_Policy_Engine `ClusterPolicy` (a cluster-scoped object, requiring the Image_Policy_Engine to be installed by the operator beforehand) that admits only images carrying a valid signature and SHALL reject any image without a valid signature.
11. THE CI_Pipeline SHALL reference each image in the Chart by its immutable content digest and SHALL NOT reference any image by a mutable tag alone.
12. THE CI_Pipeline SHALL sign images with an OIDC keyless identity, and SHALL grant the workflow least-privilege permissions in which `id-token: write` is set only in the signing job.
13. WHEN an image is signed, THE CI_Pipeline SHALL attach a provenance attestation to the image by digest.
14. WHEN the CI_Pipeline deploys the Chart or runs the Upgrade_Test, THE CI_Pipeline SHALL verify the signature of each image it installs, and SHALL fail the step if a signature is missing or invalid.

### Requirement 13: Observability

**User Story:** As an SRE, I want traces, metrics and structured logs, so that I can operate and debug the platform.

#### Acceptance Criteria

1. WHERE a collector endpoint is set in the Values_File, THE Api and THE Worker SHALL export OpenTelemetry traces to that endpoint.
2. IF the collector endpoint is unreachable or rejects an export, THEN THE Api and THE Worker SHALL drop the affected spans after at most 3 export attempts, continue serving requests and processing jobs, and keep request and job latency unchanged by more than 50 ms at the 95th percentile.
3. THE Api and THE Worker SHALL expose Prometheus metrics on a dedicated metrics port that is separate from the port serving learner traffic.
4. THE Api and THE Worker SHALL expose the Lab_Metrics on the metrics port.
5. WHERE a Values_File key selects ServiceMonitor mode, THE Chart SHALL render a `ServiceMonitor` and no scrape annotations.
6. WHERE a Values_File key selects scrape-annotation mode, THE Chart SHALL render scrape annotations on the Api and Worker pods and no `ServiceMonitor`.
7. IF the Values_File key selecting the scrape method holds a value other than ServiceMonitor mode or scrape-annotation mode, THEN THE Chart SHALL fail rendering with an error identifying the key and the two accepted values, and SHALL render no resources.
8. THE Api and THE Worker SHALL write each log record as a single-line JSON object, include the request identifier on every request-scoped record, and include the Lab_Session identifier on every lab-scoped record.
9. WHEN the Api receives a request that carries no request identifier, THE Api SHALL generate a new request identifier for that request.
10. WHEN the Api receives a request, THE Api SHALL propagate the request identifier into every Worker job it enqueues for that request and into the trace context, so that Worker log records for the job carry the same request identifier and Worker spans belong to the same trace.
11. THE Api and THE Worker SHALL exclude terminal I/O, secrets and learner personal data from log records, trace attributes and metric labels.
12. THE Lab_Metrics SHALL NOT use the learner identifier, or any other learner personal data, as a label.

### Requirement 14: Graceful shutdown and terminal drain

**User Story:** As a learner, I want my terminal to reconnect when a pod restarts, so that I do not lose my session.

#### Acceptance Criteria

1. WHEN the Api receives SIGTERM, THE Api SHALL fail its readiness probe, and SHALL start the drain no earlier than the moment the readiness probe first reports not-ready.
2. WHEN the drain starts, THE Api SHALL stop accepting new terminal WebSocket connections, send a reconnect message on each open terminal WebSocket, and close each socket within the drain period set in values (default 30 seconds, minimum 5 seconds, maximum 300 seconds).
3. IF a terminal WebSocket is still open when the drain period elapses, THEN THE Api SHALL force-close that socket, and the learner's lab session SHALL remain active and reattachable.
4. THE Chart SHALL set `terminationGracePeriodSeconds` on the Api to a value at least 10 seconds greater than the sum of the preStop delay and the drain period.
5. WHEN the web client receives the reconnect message, THE web client SHALL show the reconnect indication within 1 second, and SHALL attempt to reconnect automatically, with the first attempt within 2 seconds and at most 10 attempts using increasing delays capped at 30 seconds between attempts.
6. IF the web client exhausts its reconnect attempts, THEN THE web client SHALL show a translated (FR/EN) error with a manual "retry" action, and SHALL keep the learner's lab session unchanged.
7. WHEN a terminal reconnects, THE Api SHALL re-verify that the authenticated session owns the lab session before attaching the terminal.
8. IF session ownership verification fails or the session has expired on reconnect, THEN THE Api SHALL reject the connection without attaching the terminal, and THE web client SHALL show a translated error indicating that re-authentication is required.
9. THE Api SHALL persist no terminal I/O during the drain or reconnect.
10. WHEN the Worker receives SIGTERM, THE Worker SHALL stop consuming new jobs, and SHALL either finish or re-queue each in-flight job within `terminationGracePeriodSeconds`, with no job lost or left in a running state.
11. IF an in-flight job has not finished when `terminationGracePeriodSeconds` minus 5 seconds has elapsed, THEN THE Worker SHALL re-queue that job with its attempt count unchanged and exit.
12. WHEN the Api begins shutdown, THE Api SHALL wait a preStop delay set in values (default 5 seconds) before the drain starts, so that the removal of the Api endpoint from the ingress propagates before new connections stop.

### Requirement 15: CI pipeline

**User Story:** As a maintainer, I want CI to build, verify and prove the deployment on every change, so that regressions are caught before release.

#### Acceptance Criteria

1. WHEN a change is pushed, THE CI_Pipeline SHALL build the Image_Set, scan each image for vulnerabilities and generate an SBOM for each image, and SHALL sign and push images only as defined in criterion 9.
2. IF the vulnerability scan reports at least one finding of CRITICAL or HIGH severity that has a fix available, THEN THE CI_Pipeline SHALL fail the run and identify the affected image and finding in the run output.
3. WHEN a change is pushed, THE CI_Pipeline SHALL run `helm lint` and `helm template` against the Chart for each of the four Values_Files, and each command SHALL exit with status 0 for the step to pass.
4. WHEN a change is pushed, THE CI_Pipeline SHALL install the Chart on a Kind_Cluster and wait up to 600 seconds for all Chart workloads to report ready.
5. IF the Chart workloads are not ready within 600 seconds of the install on the Kind_Cluster, THEN THE CI_Pipeline SHALL mark the install step as failed and skip the E2E_Test, the Upgrade_Test and the Load_Test.
6. WHEN all Chart workloads on the Kind_Cluster report ready, THE CI_Pipeline SHALL run the E2E_Test, the Upgrade_Test and the Load_Test.
7. IF any step of Requirements 15.1 to 15.6 fails, THEN THE CI_Pipeline SHALL mark the run as failed and publish the step logs and the Kubernetes event dump as run artifacts, retained for at least 14 days.
8. WHEN a change is pushed from a branch other than the default branch and that is not a release tag, THE CI_Pipeline SHALL run all verification steps, SHALL verify the images against the local-registry digests, SHALL record in the run output that signature verification was skipped, and SHALL NOT sign images or push images to a registry.
9. WHEN a change is pushed to the default branch or a release tag is pushed, THE CI_Pipeline SHALL sign and push the Image_Set after the verification steps pass.
10. WHEN a CI run completes, whether it passed or failed, THE CI_Pipeline SHALL publish the Load_Test results as a run artifact, or SHALL record in the run output that no Load_Test results were produced if the Load_Test did not run.
11. THE CI_Pipeline SHALL reference every third-party action by a full 40-character commit SHA, and SHALL fail the run if any third-party action is referenced by a tag or branch name.

### Requirement 16: Kind (dev) target and isolation verification

**User Story:** As a developer, I want the stack to run on kind with real isolation checks, so that I can verify the Kubernetes controls without a cloud.

#### Acceptance Criteria

1. WHEN the Chart is installed on a Kind_Cluster with `values-dev.yaml`, THE stack SHALL reach a state where every Platform workload reports Ready within 600 seconds of the install command completing, AND THE E2E_Test SHALL pass with 0 failed assertions.
2. IF the stack does not reach the Ready state within 600 seconds of the install command completing, THEN THE E2E_Test SHALL fail, report the names of the workloads that are not Ready, and leave the Kind_Cluster in place for inspection.
3. THE Kind_Cluster configuration SHALL install a CNI that enforces NetworkPolicy, AND THE isolation test suite SHALL assert, before any other isolation assertion runs, that a NetworkPolicy denying all traffic blocks a probe connection between two test pods.
4. WHEN a Lab_Pod runs on the Kind_Cluster, THE isolation test suite SHALL assert that the Lab_Pod runs as a non-root user (UID other than 0), has no service account token mounted, fails to open a connection to an internet address within 10 seconds, fails to open a connection to a Service in any namespace other than the Labs_Namespace within 10 seconds, and is deleted within 60 seconds after its TTL expires (the TTL may be shortened as allowed by criterion 11).
5. IF any one of the Lab_Pod isolation assertions in criterion 4 does not hold, THEN THE isolation test suite SHALL fail and report which assertion failed.
6. WHERE gVisor cannot run on the Kind_Cluster, THE Kind_Cluster SHALL use plain runc, THE Worker SHALL emit the Runc_Warning once at startup, and THE Phase_5_Report SHALL record the fallback to runc and the reason gVisor could not run.
7. WHEN the Worker ServiceAccount token is used on the Kind_Cluster to read or create a resource outside the Labs_Namespace, THE Kubernetes API SHALL deny the request with an authorization denial (Forbidden), AND THE isolation test SHALL assert the denial for at least one read request and one create request.
8. THE isolation test suite SHALL assert, by attempting a connection with a timeout of 10 seconds, that every Platform_Policies path not listed in Requirements 11.2 to 11.6 is blocked, AND THE isolation test suite SHALL fail if any such path accepts a connection.
9. THE kind harness SHALL install the prerequisites the Chart relies on: metrics-server for the HorizontalPodAutoscalers, ingress-nginx, a bundled non-production PostgreSQL and Redis for `values-dev.yaml`, and the CRDs for `ExternalSecret`, Gateway API and `ServiceMonitor` for each of those kinds that the Chart renders, AND THE kind harness SHALL preload the lab images into the Kind_Cluster nodes so that the Sandbox_Ready_Time excludes registry pulls.
10. THE isolation test suite SHALL assert that the Pod_Layout works under the restricted Pod Security profile: a learner process cannot read the grader or owner mounts, cannot signal or read the environment of grader or owner processes (no cross-container process visibility), and the freeze of learner processes succeeds with all capabilities dropped. IF any of these assertions fails, THEN the Pod_Layout SHALL be changed and the grading semantics SHALL remain unchanged.
11. WHERE the CI_Pipeline runs on the Kind_Cluster, THE CI_Pipeline MAY shorten the Session_TTL through a values override applied only to the kind test values, AND THE production Values_Files SHALL keep the Session_TTL as defined in `domain/config.py`.
12. THE Pod_Layout SHALL be chosen by a day-one spike on the Kind_Cluster, SHALL prefer a layout without a shared process namespace in which the freeze runs inside the learner container through `exec`, and SHALL be recorded in `docs/adr/0009` before any Kubernetes_Provider code is built.

### Requirement 17: Deployment guide

**User Story:** As an operator, I want a complete deployment guide, so that I can install, upgrade and size the platform on my Target.

#### Acceptance Criteria

1. THE Deployment_Guide SHALL reside at `docs/DEPLOYMENT.md`.
2. THE Deployment_Guide SHALL list, for each of the three Targets (EKS, AKS and on-premise), the prerequisites: a CNI with NetworkPolicy enforcement, the Lab_RuntimeClass, the ingress or Gateway API controller, External Secrets Operator and the Lab_Node_Pool, plus the Image_Policy_Engine listed as an optional prerequisite required only WHERE `imagePolicy.enabled` is true, and SHALL state for each prerequisite the minimum version or the verification command that confirms it is present.
3. THE Deployment_Guide SHALL describe, for each of the three Targets, the ordered install steps, the ordered upgrade steps, and the rollback steps that restore the previously installed release, and SHALL state for each procedure the command or check that confirms success.
4. THE Deployment_Guide SHALL describe autoscaling settings for the Api, the Worker and the Lab_Node_Pool, stating for each the minimum replica or node count, the maximum replica or node count, and the metric and target value that triggers scaling.
5. THE Deployment_Guide SHALL state the Session_Cost as a numeric CPU, memory and storage amount per Lab_Session, and SHALL show a worked sizing example that derives the required Lab_Node_Pool node count from the Session_Cost and a stated number of concurrent Lab_Sessions, including the arithmetic for each step.
6. THE Deployment_Guide SHALL include the Go_Live_Checklist as a list in which each item has a check box and a pass condition that is verifiable by a command or an observable result.
7. THE Go_Live_Checklist SHALL include the item to verify the Lab_RuntimeClass on at least one real Target (not a local development cluster) before go-live.
8. THE Go_Live_Checklist SHALL include one separate item for each of: NetworkPolicy enforcement, External Secrets, image signature verification, TLS, WebSocket timeouts, RBAC review, PSA labels, ResourceQuota values and removal of the Runc_Warning.
9. THE Deployment_Guide SHALL state that cluster provisioning, warm pool, multi-region and backups are outside this release.
10. IF a Go_Live_Checklist item fails its pass condition, THEN THE Deployment_Guide SHALL state that go-live is blocked until that item passes and SHALL identify the corrective section of the guide for that item.

### Requirement 18: README

**User Story:** As a new contributor, I want a final README, so that I can understand and run the project.

#### Acceptance Criteria

1. THE repository README SHALL be written in English, except for the French section required by criterion 2.
2. THE repository README SHALL include one French section that summarizes the purpose of the platform, the quick start steps and a link to the Deployment_Guide, and the French section SHALL contain no more than 300 words.
3. THE repository README SHALL include a link to each of `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md` and every steering file under `.kiro/steering/`.
4. WHEN a contributor follows the quick start steps in the README on a clean checkout, THE repository README SHALL lead the contributor to a running local project without requiring any step that is not documented in the README or in a document the README links to.
5. IF a link in the README targets a file that does not exist in the repository, THEN THE repository README verification SHALL report the broken link and the README SHALL be treated as not accepted until every link resolves.
6. THE repository README SHALL include a project purpose statement of no more than 100 words in the English section, stating that EDU-IT is a bilingual (French/English) re-learning platform for IT professionals that runs on Kubernetes.

### Requirement 19: Failure scenario, lab node drained or failed

**User Story:** As a learner, I want a clear outcome if my lab node fails, so that I can recover and keep my progress.

#### Acceptance Criteria

1. IF a Lab_Node_Pool node is drained or fails while Lab_Sessions are active, THEN THE Api SHALL close each affected terminal WebSocket within 10 s of detecting the node drain or failure, sending a translated (FR or EN, per the learner's locale) "session interrupted" message and a reconnect indication, and THE Api SHALL NOT include raw infrastructure details in the message.
2. IF the Lab_Pod no longer exists after the node is drained or fails, THEN THE Worker SHALL mark the Lab_Session as ended-by-infrastructure within 30 s of detecting the loss, and THE Worker SHALL retain the learner's recorded progress, mastery and prior graded attempts unchanged.
3. IF a Lab_Session ends by infrastructure loss, THEN THE Worker SHALL release the Active_Session slot of the learner within 30 s of marking the Lab_Session ended-by-infrastructure, so that the learner can start a new Lab_Session without waiting for any cooldown.
4. IF a Lab_Session ends by infrastructure loss, THEN THE Worker SHALL NOT count the loss as a failed grading attempt, SHALL NOT reduce the learner's remaining grading attempts, and SHALL NOT change the mastery score of the learner.
5. IF a Lab_Pod is evicted, THEN THE Reaper SHALL remove the evicted pod object within 60 s of detecting the eviction.
6. WHERE a Values_File enables drain protection, THE Chart SHALL render a PodDisruptionBudget or drain annotation so that a planned node drain waits for active Lab_Sessions to finish, for at most the drain wait limit set in the Values_File.
7. WHEN a Lab_Session ends by infrastructure loss, THE Worker SHALL increment the Lab_Metrics counter for infrastructure loss by exactly 1 for that Lab_Session, regardless of how many times the loss is detected.
8. IF the drain wait limit in the Values_File is exceeded while a Lab_Session is still active, THEN THE Worker SHALL end the Lab_Session as ended-by-infrastructure and apply criteria 1 to 4 and 7.

### Requirement 20: Failure scenario, Api or Worker pod restart during active terminal sessions

**User Story:** As a learner, I want my lab to survive a platform pod restart, so that I do not lose work in the sandbox.

#### Acceptance Criteria

1. IF an Api pod restarts while terminal sessions are active, THEN THE web client SHALL show the reconnect indication within 2 seconds of the WebSocket closing and SHALL retry the connection at intervals of at most 5 seconds for up to 60 seconds, and THE Api SHALL re-verify session ownership and re-attach the terminal to the surviving Lab_Pod within 5 seconds of a successful reconnect.
2. IF the web client cannot reconnect within 60 seconds, THEN THE web client SHALL show a translated, actionable error indicating the terminal is unavailable and offering a manual retry, and THE Lab_Pod SHALL remain running until its TTL or idle timeout.
3. IF an Api pod is killed without a drain, THEN THE Lab_Pod SHALL keep running until the TTL or idle timeout and THE Reaper SHALL not delete the Lab_Pod while the Lab_Session is active.
4. IF session ownership re-verification fails on reconnect, THEN THE Api SHALL refuse the re-attach, close the WebSocket, and leave the Lab_Pod unchanged.
5. IF a Worker pod restarts while a provisioning job is in flight, THEN THE Worker SHALL re-run the job without creating a second Lab_Pod for the same Lab_Session, so that exactly one Lab_Pod exists for that Lab_Session after the job completes.
6. IF a Worker pod restarts while a Grading_Run is in flight, THEN THE Worker SHALL record the Grading_Run as failed with the translated "could not complete" feedback, SHALL remove the grading bundle from the Lab_Pod, and SHALL NOT re-run the Grading_Run automatically.
7. IF a Worker pod restarts, THEN THE Worker SHALL resume the Reaper from persisted Lab_Session state, without relying on in-memory state, within 60 seconds of the Worker becoming ready.
8. WHILE an Api or Worker workload is subject to a voluntary disruption, THE Chart PodDisruptionBudget SHALL keep at least one replica of that workload available.

### Requirement 21: Failure scenario, migration fails during Helm upgrade

**User Story:** As an operator, I want a failed migration to leave the running release intact, so that active sessions are not dropped.

#### Acceptance Criteria

1. IF the Migration_Job fails during `helm upgrade`, THEN THE Chart SHALL leave the previous release Deployments running with their pod count unchanged, SHALL NOT start or roll out any new-release Api or Worker pods, and SHALL report the upgrade as failed with a non-zero exit status from `helm upgrade`.
2. IF the Migration_Job fails, THEN THE Migration_Job SHALL exit with a non-zero status and SHALL write the failure detail (failing migration identifier and database error text) to its log, retained for at least 24 hours or until the Job is deleted.
3. WHEN the Migration_Job applies a migration on a database that supports transactional DDL, THE Migration_Job SHALL apply that migration in a single transaction and SHALL roll the transaction back if any statement in it fails, so that a failed migration leaves the schema identical to its state before that migration started.
4. THE Chart SHALL set the Migration_Job `backoffLimit` (integer, 0 to 6, default 0) and `activeDeadlineSeconds` (integer, 30 to 3600, default 600) from values.
5. IF the Migration_Job does not complete within `activeDeadlineSeconds`, THEN THE Chart SHALL treat the Migration_Job as failed and apply the behavior defined in criteria 1 and 2.
6. THE migrations SHALL remain backward compatible with the previous release for one release, so that the previous Api and Worker keep working, with no failed requests caused by schema incompatibility, during and after a failed upgrade.
7. IF an upgrade fails, THEN THE Deployment_Guide SHALL describe the rollback steps (including the `helm rollback` command and the check that the release revision returned to the previous one) and the check that Lab_Sessions are still active (the count of active Lab_Sessions equals the count recorded before the upgrade).
8. WHEN a Helm upgrade succeeds, THE Lab_Sessions that were active before the upgrade SHALL remain active after the upgrade, with none terminated or reset by the upgrade.

### Requirement 22: Failure scenario, labs namespace hits its ResourceQuota

**User Story:** As a learner, I want a clear message when the platform is at capacity, so that I know to retry later.

#### Acceptance Criteria

1. IF the Labs_Namespace ResourceQuota rejects the creation of a Lab_Pod, THEN THE Kubernetes_Provider SHALL raise a capacity failure that is a distinct error type from every other provisioning failure, and THE Kubernetes_Provider SHALL NOT classify any non-quota rejection as a capacity failure.
2. IF a capacity failure occurs, THEN THE Worker SHALL make the translated "labs are at capacity, try again shortly" message, in the learner's selected locale (FR or EN), available to the learner within 5 seconds of the failure, without raw Kubernetes output, and SHALL keep the quota details in logs only.
3. IF a capacity failure occurs, THEN THE Worker SHALL delete any Lab_Pod and associated resources created for that provisioning request within 30 seconds, so that zero partial Lab_Pods remain running for that request.
4. IF a capacity failure occurs, THEN THE Worker SHALL NOT decrement or count the failure against the learner's provisioning retry allowance, so that the allowance is identical before and after the capacity failure.
5. IF a capacity failure occurs, THEN THE Worker SHALL increment the Lab_Metrics quota rejection counter by exactly 1 per rejected provisioning attempt.
6. IF a capacity failure occurs and the elapsed time since the first provisioning attempt is less than the provisioning timeout, THEN THE Worker SHALL re-queue the provisioning request with a delay equal to the backoff value defined in `domain/config.py`.
7. IF a capacity failure occurs and the elapsed time since the first provisioning attempt is equal to or greater than the provisioning timeout, THEN THE Worker SHALL stop re-queuing, mark the provisioning request as failed with the capacity reason, and keep the translated capacity message available to the learner.
8. IF a Lab_Pod remains in the Pending state because the Lab_Node_Pool lacks capacity, THEN THE Kubernetes_Provider SHALL, when the provisioning timeout elapses, raise a capacity failure and delete the Pending Lab_Pod.
9. WHERE a Values_File enables Prometheus rules, THE Chart SHALL expose an alert rule that fires when the quota rejection counter increases in each of the evaluation windows across a sustained period whose duration and threshold are set in the Values_File.

### Requirement 23: Failure scenario, External Secrets cannot fetch a secret at startup

**User Story:** As an operator, I want a missing secret to stop the rollout visibly, so that the platform never starts with wrong credentials.

#### Acceptance Criteria

1. IF an `ExternalSecret` cannot be synced, THEN THE Kubernetes Secret SHALL be absent AND THE dependent Api or Worker pod SHALL remain in a not-Ready state (Ready condition `False`) without being restarted into a running state with default or empty credentials.
2. IF a required secret is missing at startup, THEN THE Api or THE Worker SHALL exit with a non-zero status within 30 seconds of process start AND log one entry per missing secret key naming the key, without logging any secret value.
3. IF a required secret is missing during `helm install`, THEN THE Chart SHALL fail the release when the Helm timeout (default 300 seconds) elapses AND report the release status as failed.
4. IF a required secret is missing during `helm upgrade`, THEN THE Chart SHALL fail the release when the Helm timeout (default 300 seconds) elapses AND keep the previous release Api and Worker pods Ready and serving, with at least one pod per Deployment available throughout.
5. IF an `ExternalSecret` sync fails, THEN THE Chart SHALL expose the failure through the `ExternalSecret` Ready condition set to `False` with a reason and message, AND WHERE a Values_File enables Prometheus rules, THE Chart SHALL include an alert rule that fires when the condition remains `False` for 5 minutes.
6. IF a secret refresh fails after a successful start, THEN THE Api and THE Worker SHALL keep running with the last synced value, remain Ready, and not restart because of the refresh failure.
7. THE Deployment_Guide SHALL describe how to diagnose an `ExternalSecret` sync failure, including how to read the `ExternalSecret` condition and the pod startup log for the missing key name, and the corrective action for each.

### Requirement 24: Performance targets

**User Story:** As a product owner, I want measurable capacity and latency targets, so that I know the deployment meets learner expectations.

#### Acceptance Criteria

1. WHEN the Load_Test runs on a Kind_Cluster, THE Sandbox_Ready_Time SHALL have a nearest-rank p95 of at most 30 s, computed over at least 20 samples, where each sample is measured from the Lab_Session start request to the Lab_Pod reporting ready.
2. WHEN the Load_Test runs on a standard CI runner, THE stack SHALL sustain at least 10 concurrent Lab_Sessions for a continuous hold period of at least 60 s, with every one of the 10 Lab_Sessions reaching a ready state within 120 s of its start request.
3. WHILE at least 10 Lab_Sessions are concurrent, THE stack SHALL meet the Phase 3 terminal and grading targets recorded in `docs/reports/phase-3.md`.
4. WHILE at least 10 Lab_Sessions are concurrent, THE Worker SHALL return each grading result within the 15 s target, measured from grading request to result availability, with a maximum observed grading latency of at most 15 s across all samples.
5. THE Load_Test SHALL record the sample count, the p50, p95 and max of the Sandbox_Ready_Time and the terminal and grading latencies, and the CPU utilization (percent) and memory usage (MiB) of the runner, sampled at intervals of at most 5 s for the duration of the run.
6. IF the Load_Test misses a target, THEN THE CI_Pipeline SHALL fail the run with a non-zero exit status and a report that names each missed target together with its measured value and its threshold.
7. THE Load_Test SHALL clean up every Lab_Pod it creates, so that zero Lab_Pods created by the Load_Test remain in the Kind_Cluster within 60 s after the run ends.
8. IF the Load_Test fails, aborts or times out before completion, THEN THE Load_Test SHALL still delete every Lab_Pod it created and THE CI_Pipeline SHALL report the run as failed.
9. IF the Load_Test cannot start 10 concurrent Lab_Sessions, THEN THE CI_Pipeline SHALL fail the run and THE Load_Test SHALL report the number of Lab_Sessions that were started.

### Requirement 25: Upgrade without dropping sessions

**User Story:** As an operator, I want to upgrade a live release, so that I can ship changes while learners work.

#### Acceptance Criteria

1. WHEN the Upgrade_Test runs, THE Upgrade_Test SHALL install the previous release, start at least 3 Lab_Sessions, wait until each Lab_Session reports a running Lab_Pod within 120 seconds, run `helm upgrade` to the candidate release including at least one migration, and verify the outcome.
2. WHEN the `helm upgrade` completes, THE Upgrade_Test SHALL verify within 120 seconds that each Lab_Session started before the upgrade still has the same running Lab_Pod (same pod identity, not restarted or rescheduled) and an attachable terminal that accepts a command and returns its output.
3. WHEN the `helm upgrade` completes, THE Upgrade_Test SHALL verify within 300 seconds that the migration is applied (the schema version equals the candidate release's version) and that every new Api and Worker pod passes its readiness probe.
4. WHILE the rolling update is in progress, THE Upgrade_Test SHALL verify that each terminal client that was connected before the upgrade reconnects within 30 seconds of its WebSocket being closed, and that the reconnected terminal accepts a command and returns its output.
5. IF the Upgrade_Test fails or any verification in criteria 1 to 4 does not complete within its time limit, THEN THE CI_Pipeline SHALL fail the run, report a non-zero exit status, and retain the Upgrade_Test logs as a run artifact.
6. THE Chart SHALL use a rolling update strategy with `maxUnavailable` set so that at least one Api replica stays Ready during the upgrade, and SHALL configure at least 2 Api replicas by default.
7. IF the migration fails during the `helm upgrade`, THEN THE Upgrade_Test SHALL report the upgrade as failed, and verify that each Lab_Session started before the upgrade still has a running Lab_Pod (no Lab_Session is terminated by the failed upgrade).
8. THE Upgrade_Test SHALL define the previous release as the chart and images of the most recent release tag, or of the base commit when no release tag exists.
9. WHEN the `helm upgrade` has completed and been verified, THE Upgrade_Test SHALL run `helm rollback` to the previous release and SHALL verify that each Lab_Session started before the upgrade remains active.

### Requirement 26: Traceability and acceptance verification

**User Story:** As a maintainer, I want every requirement claimed by a verification, so that nothing is left unproven.

#### Acceptance Criteria

1. THE repository SHALL contain at least one verification for each acceptance criterion of Requirements 1 to 25, and THE traceability table checked by `tools/check_traceability.py` SHALL claim each such criterion by its id exactly once or more, with zero acceptance criteria of Requirements 1 to 25 left unclaimed.
2. IF `tools/check_traceability.py` finds an acceptance criterion of Requirements 1 to 25 that no verification claims, or a claim that references a non-existent criterion id, THEN THE `tools/check_traceability.py` check SHALL exit with a non-zero status and SHALL list each unclaimed or unknown id.
3. WHEN Milestone A, B or C completes, THE Phase_5_Report SHALL list, for that milestone, each command run and the outcome of each check as one of passed, failed or not run.
4. THE Phase_5_Report SHALL include a Not verified section that lists every required check that was not run, including the real-target RuntimeClass check and any gVisor fallback, and SHALL state for each entry the reason it was not run.
5. THE Phase_5_Report SHALL state a passing status only when every required check ran and passed, and IF any required check failed or was not run, THEN THE Phase_5_Report SHALL state a not-passing status.
6. THE Phase_5_Report SHALL record the Load_Test results and the Sandbox_Ready_Time p95 in seconds, and IF the Load_Test or the Sandbox_Ready_Time measurement was not run, THEN THE Phase_5_Report SHALL record it as not run in the Not verified section instead of reporting a value.
7. THE Phase_5_Report SHALL include a next-steps list ordered by priority, with each item numbered by rank, covering at least the placement assessment, the project reviewers and the warm pool.
8. THE final task of the implementation plan SHALL write `docs/reports/phase-5.md`.
9. WHEN the Chart, the images, the CI_Pipeline and the Deployment_Guide are added, THE existing application tests SHALL pass with zero failures and zero tests removed or skipped to achieve the pass.
10. THE implementation SHALL not implement cluster provisioning, the warm pool, multi-region or backups.