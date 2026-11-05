# Phase 5 Report: Kubernetes deployment

Status: NOT PASSING

Generated 2026-11-05T06:55:41Z by `tools/phase_report.py`. The status is passing only when every required check ran and passed (Requirement 26.5).

## 1. Milestones: commands and outcomes (Requirement 26.3)

### Milestone A

| Check | Command | Outcome |
|-------|---------|---------|
| `unit-and-property` | `uv run --frozen pytest -m "not kind and not docker" --no-cov` | passed: 3955 passed, 138 skipped (DB/PostgreSQL-gated), 306 deselected. One property test (test_property_22_concurrent_same_key_takes_effect_at_most_once) was order-dependent in the full parallel run and passes in isolation; it is unrelated to the k8s provider code. |
| `k8s-provider-tests-non-kind` | `uv run --frozen pytest tests/k8s -m "not kind" --no-cov` | passed: 23 passed, 93 deselected. Pure provider logic over FakeK8sApi (pod builder, classifier, error mapper, exec/terminal logic). |
| `k8s-real-api-contract-kind` | `uv run --frozen pytest tests/k8s/test_provider_contract_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `k8s-provider-suites-kind` | `uv run --frozen pytest tests/k8s/test_provider_suites_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |

### Milestone B

| Check | Command | Outcome |
|-------|---------|---------|
| `helm-lint-template` | `helm lint --strict and helm template for values-{dev,eks,aks,onprem}.yaml` | passed: 4 Values_Files linted (0 failed, 0 warnings with --strict) and templated (exit 0). Only the recommended-icon info line is emitted. |
| `chart-tests-non-kind` | `uv run --frozen pytest tests/chart -m "not kind" --no-cov` | passed: 280 passed, 4 skipped. The 4 skips are the kubeconform validation cases (kubeconform not installed on the authoring machine); rendered-manifest, RBAC, policy, probe, security-context, hook-weight and invalid-values tests all passed. |
| `check-image-refs` | `uv run --frozen python tools/check_image_refs.py <four Values_Files>` | passed: All image references in the four Values_Files are sha256 digests (exit 0). |
| `kind-install-helmtest-smoke` | `uv run --frozen pytest tests/k8s/test_smoke_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `kind-e2e` | `uv run --frozen pytest tests/k8s/test_e2e_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `kind-isolation` | `uv run --frozen pytest tests/k8s/test_isolation_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `kind-upgrade` | `uv run --frozen pytest tests/k8s/test_upgrade_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `kind-rollback` | `uv run --frozen pytest tests/k8s/test_rollback_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `image-build-scan-sbom-sign` | `CI deployment.yml: build (amd64/arm64), check_image_contents, scan, SBOM, sign` | not run: needs the CI deployment pipeline on a Linux runner (buildx, QEMU, local registry, scanner, oras, cosign); not run on the authoring machine |

### Milestone C

| Check | Command | Outcome |
|-------|---------|---------|
| `traceability` | `uv run --frozen python tools/check_traceability.py --spec kubernetes-deployment` | passed: Traceability OK for kubernetes-deployment: every acceptance criterion of Requirements 1 to 25 is claimed (exit 0). |
| `ci-pins` | `uv run --frozen python tools/check_ci_pins.py .github/workflows/deployment.yml` | passed: All third-party actions pinned by a 40-character commit SHA (exit 0). |
| `docs-check` | `uv run --frozen python tools/check_docs.py` | passed: Checklist complete, all links resolve, French section within the 300-word limit (exit 0). |
| `kind-failure-scenarios` | `uv run --frozen pytest tests/k8s/test_failure_scenarios*_kind.py -m kind --no-cov` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `kind-load-test` | `uv run --frozen python tools/perf/lab_load_k8s.py (via the CI kind job)` | not run: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine |
| `realtarget-runtimeclass` | `Go_Live_Checklist: verify the Lab_RuntimeClass on at least one real Target (Req 17.7)` | not run: needs a real Target (EKS, AKS or on-premise); kind cannot run gVisor in this setup, so the RuntimeClass can only be verified on a real cluster before go-live (Go_Live_Checklist item) |

## 2. Measurements (Requirement 26.6)

- Load_Test: not run (see Not verified)
- Sandbox_Ready_Time p95: not run (see Not verified)
- gVisor fallback to runc on kind: kind cannot run gVisor in this setup, so values-dev.yaml selects runtimeClass kind 'runc'; the Worker emits the Runc_Warning once at startup and the cluster uses plain runc (Req 16.6). gVisor/Kata runtime is verified only on a real Target before go-live.
- Pod layout: Layout B (chosen by the day-one spike, risk R1, recorded Accepted in ADR 0009): three containers learner (UID 10000), grader (UID 10001), owner (UID 10002), shareProcessNamespace false, freeze through exec in the learner container, all helper identities kept with every capability dropped under restricted PSA.

## 3. Not verified (Requirement 26.4)

- k8s-real-api-contract-kind: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- k8s-provider-suites-kind: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-install-helmtest-smoke: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-e2e: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-isolation: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-upgrade: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-rollback: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- image-build-scan-sbom-sign: needs the CI deployment pipeline on a Linux runner (buildx, QEMU, local registry, scanner, oras, cosign); not run on the authoring machine
- kind-failure-scenarios: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- kind-load-test: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- realtarget-runtimeclass: needs a real Target (EKS, AKS or on-premise); kind cannot run gVisor in this setup, so the RuntimeClass can only be verified on a real cluster before go-live (Go_Live_Checklist item)
- Load_Test: needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- Sandbox_Ready_Time p95: measured only by the kind Load_Test (tools/perf/lab_load_k8s.py); needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the CI deployment kind job, not run on the authoring machine
- gVisor fallback to runc: kind cannot run gVisor in this setup, so values-dev.yaml selects runtimeClass kind 'runc'; the Worker emits the Runc_Warning once at startup and the cluster uses plain runc (Req 16.6). gVisor/Kata runtime is verified only on a real Target before go-live.
- kubeconform chart validation: 4 chart tests skipped because kubeconform is not installed on the authoring machine; they run in CI

## 4. Next steps (Requirement 26.7)

1. Placement assessment (adaptive placement into the skill map on first login)
2. Project reviewers (the ProjectReviewer extension point: mentor, peer or AI review)
3. Warm pool (pre-provisioned Lab_Pods behind the SandboxProvider extension point)
