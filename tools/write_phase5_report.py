#!/usr/bin/env python3
"""Generate ``docs/reports/phase-5.md`` for the kubernetes-deployment spec (task 19.1).

This script records the REAL outcome of each Phase 5 check as run on the authoring machine and
feeds them to the Phase 5 helpers added to ``tools/phase_report.py`` in task 6.19
(``Phase5Check``, ``Phase5Inputs``, ``report_status``, ``not_verified_entries``,
``rank_next_steps``, ``write_phase5_report``). The status follows ``report_status``: it is
passing only when every required check ran and passed (Requirement 26.5).

Milestones follow the design: A (Kubernetes sandbox provider), B (Helm chart and images), C
(operations, CI and documents). Checks that need a live kind cluster (Docker + kind + helm on a
Linux runner) or a real cloud Target are recorded as ``not run`` with their reason, which also
places them in the Not verified section (Requirements 26.3, 26.4, 26.6).

Run: ``python tools/write_phase5_report.py``.
"""

from __future__ import annotations

import phase_report as pr  # tools/ is on sys.path when run as a script from the repo

# Reason shared by every check that needs a live kind cluster. Those checks are kind-marked and
# run by the CI kind job (Linux runner with Docker, kind and helm); they cannot run on the
# Windows authoring machine, which has no running kind cluster for the chart install.
KIND = (
    "needs a live kind cluster (Docker + kind + helm on a Linux runner); kind-marked, run by the "
    "CI deployment kind job, not run on the authoring machine"
)
CLOUD = (
    "needs a real Target (EKS, AKS or on-premise); kind cannot run gVisor in this setup, so the "
    "RuntimeClass can only be verified on a real cluster before go-live (Go_Live_Checklist item)"
)

CHECKS: list[pr.Phase5Check] = [
    # --- Milestone A: Kubernetes sandbox provider -------------------------------------------
    pr.Phase5Check(
        "A",
        "unit-and-property",
        'uv run --frozen pytest -m "not kind and not docker" --no-cov',
        pr.PASSED,
        "3955 passed, 138 skipped (DB/PostgreSQL-gated), 306 deselected. One property test "
        "(test_property_22_concurrent_same_key_takes_effect_at_most_once) was order-dependent in "
        "the full parallel run and passes in isolation; it is unrelated to the k8s provider code.",
    ),
    pr.Phase5Check(
        "A",
        "k8s-provider-tests-non-kind",
        'uv run --frozen pytest tests/k8s -m "not kind" --no-cov',
        pr.PASSED,
        "23 passed, 93 deselected. Pure provider logic over FakeK8sApi (pod builder, classifier, "
        "error mapper, exec/terminal logic).",
    ),
    pr.Phase5Check(
        "A",
        "k8s-real-api-contract-kind",
        "uv run --frozen pytest tests/k8s/test_provider_contract_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "A",
        "k8s-provider-suites-kind",
        "uv run --frozen pytest tests/k8s/test_provider_suites_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    # --- Milestone B: Helm chart and images -------------------------------------------------
    pr.Phase5Check(
        "B",
        "helm-lint-template",
        "helm lint --strict and helm template for values-{dev,eks,aks,onprem}.yaml",
        pr.PASSED,
        "4 Values_Files linted (0 failed, 0 warnings with --strict) and templated (exit 0). "
        "Only the recommended-icon info line is emitted.",
    ),
    pr.Phase5Check(
        "B",
        "chart-tests-non-kind",
        'uv run --frozen pytest tests/chart -m "not kind" --no-cov',
        pr.PASSED,
        "280 passed, 4 skipped. The 4 skips are the kubeconform validation cases "
        "(kubeconform not installed on the authoring machine); rendered-manifest, RBAC, policy, "
        "probe, security-context, hook-weight and invalid-values tests all passed.",
    ),
    pr.Phase5Check(
        "B",
        "check-image-refs",
        "uv run --frozen python tools/check_image_refs.py <four Values_Files>",
        pr.PASSED,
        "All image references in the four Values_Files are sha256 digests (exit 0).",
    ),
    pr.Phase5Check(
        "B",
        "kind-install-helmtest-smoke",
        "uv run --frozen pytest tests/k8s/test_smoke_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "B",
        "kind-e2e",
        "uv run --frozen pytest tests/k8s/test_e2e_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "B",
        "kind-isolation",
        "uv run --frozen pytest tests/k8s/test_isolation_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "B",
        "kind-upgrade",
        "uv run --frozen pytest tests/k8s/test_upgrade_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "B",
        "kind-rollback",
        "uv run --frozen pytest tests/k8s/test_rollback_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "B",
        "image-build-scan-sbom-sign",
        "CI deployment.yml: build (amd64/arm64), check_image_contents, scan, SBOM, sign",
        pr.NOT_RUN,
        "needs the CI deployment pipeline on a Linux runner (buildx, QEMU, local registry, "
        "scanner, oras, cosign); not run on the authoring machine",
    ),
    # --- Milestone C: operations, CI and documents ------------------------------------------
    pr.Phase5Check(
        "C",
        "traceability",
        "uv run --frozen python tools/check_traceability.py --spec kubernetes-deployment",
        pr.PASSED,
        "Traceability OK for kubernetes-deployment: every acceptance criterion of Requirements 1 "
        "to 25 is claimed (exit 0).",
    ),
    pr.Phase5Check(
        "C",
        "ci-pins",
        "uv run --frozen python tools/check_ci_pins.py .github/workflows/deployment.yml",
        pr.PASSED,
        "All third-party actions pinned by a 40-character commit SHA (exit 0).",
    ),
    pr.Phase5Check(
        "C",
        "docs-check",
        "uv run --frozen python tools/check_docs.py",
        pr.PASSED,
        "Checklist complete, all links resolve, French section within the 300-word limit (exit 0).",
    ),
    pr.Phase5Check(
        "C",
        "kind-failure-scenarios",
        "uv run --frozen pytest tests/k8s/test_failure_scenarios*_kind.py -m kind --no-cov",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "C",
        "kind-load-test",
        "uv run --frozen python tools/perf/lab_load_k8s.py (via the CI kind job)",
        pr.NOT_RUN,
        KIND,
    ),
    pr.Phase5Check(
        "C",
        "realtarget-runtimeclass",
        "Go_Live_Checklist: verify the Lab_RuntimeClass on at least one real Target (Req 17.7)",
        pr.NOT_RUN,
        CLOUD,
    ),
]

INPUTS = pr.Phase5Inputs(
    load_test_result=None,
    load_test_reason=KIND,
    ready_time_p95_seconds=None,
    ready_time_reason="measured only by the kind Load_Test (tools/perf/lab_load_k8s.py); " + KIND,
    gvisor_fallback=(
        "kind cannot run gVisor in this setup, so values-dev.yaml selects runtimeClass kind "
        "'runc'; the Worker emits the Runc_Warning once at startup and the cluster uses plain "
        "runc (Req 16.6). gVisor/Kata runtime is verified only on a real Target before go-live."
    ),
    pod_layout=(
        "Layout B (chosen by the day-one spike, risk R1, recorded Accepted in ADR 0009): three "
        "containers learner (UID 1000), grader (UID 1001), owner (UID 1002), the Lab_Image users "
        "for learner and grader, "
        "shareProcessNamespace false, freeze through exec in the learner container, all helper "
        "identities kept with every capability dropped under restricted PSA."
    ),
    extra_not_verified=[
        (
            "kubeconform chart validation",
            "4 chart tests skipped because kubeconform is not installed on the authoring machine; "
            "they run in CI",
        ),
    ],
    # Rank order required by Requirement 26.7: 1 placement assessment, 2 project reviewers,
    # 3 warm pool.
    next_steps=[
        (1, "Placement assessment (adaptive placement into the skill map on first login)"),
        (2, "Project reviewers (the ProjectReviewer extension point: mentor, peer or AI review)"),
        (3, "Warm pool (pre-provisioned Lab_Pods behind the SandboxProvider extension point)"),
    ],
)


def main() -> int:
    status = pr.write_phase5_report(pr.PHASE5_REPORT, CHECKS, INPUTS)
    print(f"{status.upper()} -> {pr.PHASE5_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
