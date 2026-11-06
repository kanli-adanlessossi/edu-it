"""Labs traceability suite (hands-on-labs task 22.1; Requirements 10.12, 29.11, 33.1, 33.2).

Every rule of ``.kiro/steering/labs.md``, the Labs section of
``.kiro/steering/domain-rules.md`` and ``docs/LABS-DESIGN.md`` section 4 (failure scenarios) is
mapped, in ``RULE_MAP``, to requirement ids of the hands-on-labs spec and to ``(test file,
needle)`` pairs. The needle must occur in the test file, so a renamed or deleted test breaks the
mapping. The security and grading-integrity rules that steering implies (non-root, no network,
four limits, capabilities, no learner code in api/worker, grading isolation, fail-closed freeze,
tamper resistance, throttles, dev-only Docker) are mapped as ``security:`` entries.

Evidence classes (Requirements 10.12, 29.11):

- Fake_Provider tests verify call logic only and are not security evidence.
- Tamper resistance is evidenced by the Docker_Provider tests (``docker`` marker).
- Security and grading-integrity rules map to the Docker tests; this suite fails when such a
  rule has no mapped test carrying the ``docker`` marker (the two exceptions, throttles and
  dev-only Docker, are pure logic and are listed in ``DOCKER_EVIDENCE_EXEMPT``).

``tools/phase_report.py`` imports ``RULE_MAP`` from this module and expects the phase-2 shape
``(rule key, requirement ids, [(test file, needle)])``.

**Validates: Requirements 10.12, 29.11, 33.1, 33.2**
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LABS_STEERING = REPO / ".kiro" / "steering" / "labs.md"
DOMAIN_RULES = REPO / ".kiro" / "steering" / "domain-rules.md"
LABS_DESIGN = REPO / "docs" / "LABS-DESIGN.md"
REQUIREMENTS = REPO / ".kiro" / "specs" / "hands-on-labs" / "requirements.md"
K8S_REQUIREMENTS = REPO / ".kiro" / "specs" / "kubernetes-deployment" / "requirements.md"
#: Prefix of a kubernetes-deployment requirement id in ``RULE_MAP``; ids without it are
#: hands-on-labs ids. Both id spaces use ``N`` and ``N.k``, so the prefix keeps them apart.
K8S_PREFIX = "kubernetes-deployment:"
THIS_FILE = Path(__file__).resolve()

#: Statement of the evidence classes; the documentation test checks it is present.
EVIDENCE_CLASSES = (
    "Fake_Provider tests verify call logic only and are not security evidence; "
    "tamper resistance is evidenced by the Docker_Provider tests, and security and "
    "grading-integrity rules map to the Docker tests"
)

RuleMap = Sequence[tuple[str, "list[str] | str", "list[tuple[str, str]]"]]

_APP = "packages/application/tests"
_DOM = "packages/domain/tests"
_INFRA = "packages/infrastructure/tests"
_API = "apps/api/tests"
_WORKER = "apps/worker/tests"
_LABS = "tests/labs"
_WEB = "apps/web/src"


def _p(n: int) -> str:
    """Needle of the Hypothesis/fast-check property tag ``Feature: hands-on-labs, Property n:``."""
    return f"hands-on-labs, Property {n}:"


_T = "def test_"

# Docker_Provider evidence (tests carry the ``docker`` marker).
_D_ISOLATION = (f"{_LABS}/test_docker_isolation.py", _T)
_D_LIFECYCLE = (f"{_LABS}/test_docker_lifecycle.py", _T)
_D_GRADING = (f"{_LABS}/test_docker_grading_isolation.py", _T)
_D_ABUSE = (f"{_LABS}/test_docker_check_abuse.py", _T)
_D_FREEZE = (f"{_LABS}/test_docker_freeze_failure.py", _T)
_D_HARNESS = (f"{_LABS}/test_docker_harness.py", _T)
_D_TERMINAL = (f"{_LABS}/test_docker_terminal.py", _T)
_D_TAMPER_1 = (f"{_LABS}/test_seed_lab_1_tamper.py", _T)
_D_TAMPER_2 = (f"{_LABS}/test_seed_lab_2_tamper.py", _T)
_D_UNSAFE = (f"{_LABS}/test_unsafe_learner_files_property.py", _p(36))
_D_CANARY = (f"{_LABS}/test_static_analysis_canary.py", _T)

# Day-one pod layout spike on kind (kubernetes-deployment task 1.2); a script, not a pytest
# module, so it is the evidence for the RBAC and admission checks until the chart tests exist.
_K_SPIKE_FILE = "tests/k8s/spike_pod_layout_kind.py"
_K8S = "tests/k8s"
_K_EXEC_TT = f"{_INFRA}/test_k8s_exec_timeout_and_terminal.py"
_K_SPIKE_RBAC = (_K_SPIKE_FILE, "def check_rbac")

#: Keys of ``security:`` entries that are pure logic and need no Docker-marked test.
DOCKER_EVIDENCE_EXEMPT = frozenset(
    {
        "security: throttles (one active session, provisioning and grading rate limits)",
        "security: the Docker provider is dev only and refused in production",
    }
)

# Evidence classes (Requirements 10.12, 29.11): Fake_Provider tests verify call logic only and
# are not security evidence; tamper resistance is evidenced by the Docker_Provider tests; the
# security and grading-integrity rules map to the Docker tests.
#
# (substring identifying a rule bullet or a ``security:`` rule, requirement ids, (test file,
# needle) pairs). A requirement id is ``N`` (the whole requirement) or ``N.k`` (one criterion).
# The Kubernetes provider is out of scope for this spec (requirements.md); the runtime and
# topology rules of labs.md are mapped to the in-scope counterparts: the port stays
# implementable, the dev-only guard, and the Sandbox lockdown the Docker_Provider enforces.
# The "Kubernetes Sandbox Provider (ADR 0009)" rules of labs.md are mapped to criteria of the
# kubernetes-deployment spec (ids prefixed with ``K8S_PREFIX``) and to the tests that carry
# the matching ``# Claims:`` tags. Those tests run over ``FakeK8sApi`` (call logic, not
# security evidence) or are the day-one kind spike script; the real-cluster runs are tasks
# 6.22 and 6.23.
RULE_MAP: list[tuple[str, list[str] | str, list[tuple[str, str]]]] = [
    # --- .kiro/steering/labs.md ---------------------------------------------------------
    (
        "Learners rebuild skills",
        ["19.1", "19.3", "19.6"],
        [
            (f"{_INFRA}/test_lab_check_gate_property.py", _p(13)),
            (f"{_DOM}/test_property_lab_mastery.py", _p(12)),
        ],
    ),
    (
        "Sandbox: Behind `SandboxProvider`",
        ["1.1", "1.2", "1.4"],
        [
            (f"{_INFRA}/test_sandbox_provider_contract.py", _T),
            (f"{_INFRA}/test_sandbox_provider_lifecycle_property.py", _p(2)),
        ],
    ),
    (
        "The sandbox sits behind",
        ["1.1", "1.2", "1.3"],
        [
            (f"{_DOM}/test_lab_ports.py", _T),
            (f"{_INFRA}/test_fake_lab_adapters.py", _T),
            (f"{_INFRA}/test_sandbox_provider_contract.py", _T),
        ],
    ),
    (
        "`SandboxProvider`: implementations",
        ["1.2", "1.5", "2.1", "2.2"],
        [
            (f"{_INFRA}/test_sandbox_provider_selection.py", _T),
            (f"{_DOM}/test_lab_ports.py", _T),
        ],
    ),
    (
        "`apps/web`: renders the xterm.js terminal",
        ["13.3", "13.4", "15.2", "15.10"],
        [
            (f"{_WEB}/components/terminal-pane.test.tsx", "describe("),
            (f"{_WEB}/lab-state.property.test.ts", _p(18)),
        ],
    ),
    (
        "`apps/api`: hosts the authenticated terminal WebSocket",
        ["12.1", "12.2", "12.10", "12.11"],
        [
            (f"{_APP}/test_terminal_property_auth.py", _p(14)),
            (f"{_API}/test_labs_terminal_route.py", _T),
        ],
    ),
    (
        "`apps/worker`: lab provisioning",
        ["6.1", "6.5", "8.1", "9.1"],
        [
            (f"{_WORKER}/test_lab_worker_wiring.py", _T),
            (f"{_APP}/test_reap_sessions_property.py", _p(5)),
            (f"{_APP}/test_provision_sandbox_properties.py", _p(8)),
        ],
    ),
    (
        "The api can attach terminal TTY",
        ["12.3", "12.4"],
        [_D_TERMINAL, (f"{_APP}/test_terminal_gateway.py", _T)],
    ),
    (
        "labs namespace",
        ["1.5", "3.4", "3.5"],
        [
            (f"{_INFRA}/test_docker_spec_properties.py", _p(1)),
            _D_ISOLATION,
        ],
    ),
    (
        "Dedicated lab node pool",
        ["1.1", "1.5", "2.1", "2.2"],
        [
            (f"{_INFRA}/test_sandbox_provider_selection.py", _T),
            (f"{_INFRA}/test_docker_provider.py", _T),
        ],
    ),
    (
        "Default-deny NetworkPolicy",
        ["3.2", "3.7"],
        [_D_ISOLATION, (f"{_INFRA}/test_docker_spec_properties.py", _p(1))],
    ),
    (
        "Graceful shutdown drains terminal WebSockets",
        ["12.8", "26.5"],
        [
            (f"{_APP}/test_terminal_property_drain.py", _p(31)),
            (f"{_API}/test_terminal_failure_scenarios.py", _T),
        ],
    ),
    (
        "The worker provisions, executes and destroys",
        ["6.2", "6.5", "8.2", "10.1"],
        [
            (f"{_APP}/test_provision_sandbox.py", _T),
            (f"{_APP}/test_reap_sessions.py", _T),
            (f"{_APP}/test_grade_run.py", _T),
        ],
    ),
    # Re-mapped from the phase-2 "out-of-scope" entry (Requirements 7.4, 11, 28).
    (
        "Grading is bounded",
        ["7.4", "11", "28"],
        [
            (f"{_LABS}/test_grading_acceptance_property.py", _p(6)),
            (f"{_APP}/test_grade_run_scheduling_properties.py", _p(11)),
            (f"{_APP}/test_grade_run_queue_delay.py", _T),
            _D_ABUSE,
        ],
    ),
    (
        "IF sandbox provisioning fails",
        ["25.1", "25.2", "25.3", "25.5"],
        [
            (f"{_LABS}/test_provisioning_failure_scenarios.py", _T),
            (f"{_APP}/test_provision_sandbox_properties.py", _p(8)),
        ],
    ),
    (
        "IF a terminal WebSocket connection drops",
        ["26.1", "26.2", "26.4", "26.5", "26.6"],
        [
            (f"{_API}/test_terminal_failure_scenarios.py", _T),
            (f"{_APP}/test_terminal_property_no_persistence.py", _p(17)),
        ],
    ),
    (
        "IF a grading check does not return",
        ["11.3", "28.1", "28.2", "28.3"],
        [_D_ABUSE, (f"{_APP}/test_grade_run_scheduling_properties.py", _p(11))],
    ),
    # --- .kiro/steering/domain-rules.md (Labs section) ------------------------------------
    (
        "Labs are hands-on",
        ["12.3", "14.3", "21.1", "22.1"],
        [_D_TERMINAL, _D_HARNESS],
    ),
    (
        "A lab check contributes",
        ["19.1", "19.3"],
        [
            (f"{_INFRA}/test_lab_check_gate_property.py", _p(13)),
            (f"{_DOM}/test_property_lab_mastery.py", _p(12)),
        ],
    ),
    # --- docs/LABS-DESIGN.md section 4 ----------------------------------------------------
    (
        "surfaces a translated error",
        ["25.2", "25.4"],
        [
            (f"{_API}/test_errors_lab_problems_properties.py", _p(30)),
            (f"{_LABS}/test_provisioning_failure_scenarios.py", _T),
        ],
    ),
    (
        "leaves no partial session running",
        ["25.3", "25.6"],
        [(f"{_APP}/test_provision_sandbox_properties.py", _p(8))],
    ),
    (
        "lets the reaper clean orphaned",
        ["8.4", "25.5"],
        [(f"{_APP}/test_reap_sessions_property.py", _p(5))],
    ),
    (
        "applies provisioning rate-limits",
        ["7.5", "7.6", "25.1"],
        [
            (f"{_DOM}/test_lab_property_provision_rate.py", _p(9)),
            (f"{_APP}/test_provision_sandbox_properties.py", _p(8)),
        ],
    ),
    (
        "Maximum provisioning retries",
        ["25.1"],
        [(f"{_APP}/test_provision_sandbox_properties.py", _p(8))],
    ),
    (
        "Maximum provisioning rate",
        ["7.5", "7.6"],
        [(f"{_DOM}/test_lab_property_provision_rate.py", _p(9))],
    ),
    (
        "re-verifies session ownership",
        ["12.11", "26.2"],
        [(f"{_APP}/test_terminal_property_auth.py", _p(14))],
    ),
    (
        "presents a reconnect indication",
        ["13.4", "26.1"],
        [
            (f"{_WEB}/components/terminal-pane.test.tsx", "describe("),
            (f"{_WEB}/lab-state.property.test.ts", _p(18)),
        ],
    ),
    (
        "drains active sockets",
        ["12.8", "26.5"],
        [(f"{_APP}/test_terminal_property_drain.py", _p(31))],
    ),
    (
        "persists no terminal I/O",
        ["12.7", "26.6"],
        [(f"{_APP}/test_terminal_property_no_persistence.py", _p(17))],
    ),
    (
        "grading is bounded (results",
        ["7.4", "11", "28"],
        [
            (f"{_LABS}/test_grading_acceptance_property.py", _p(6)),
            (f"{_APP}/test_grade_run_scheduling_properties.py", _p(11)),
            _D_ABUSE,
        ],
    ),
    (
        "the timeout yields a translated",
        ["18.3", "28.2", "28.7"],
        [(f"{_DOM}/test_lab_property_learner_view.py", _p(10)), _D_ABUSE],
    ),
    (
        "the run is recorded with a failed status",
        ["11.4", "28.3"],
        [(f"{_APP}/test_grade_run_scheduling_properties.py", _p(11)), _D_ABUSE],
    ),
    # --- Security and grading-integrity rules (Docker evidence, Requirements 10.12, 29.11) -
    (
        "security: Sandbox processes run as a non-root user",
        ["3.1", "3.10"],
        [_D_ISOLATION, (f"{_INFRA}/test_docker_spec_properties.py", _p(1))],
    ),
    (
        "security: Sandbox has no network beyond loopback",
        ["3.2", "3.7", "3.10"],
        [_D_ISOLATION],
    ),
    (
        "security: the four resource limits (CPU, memory, PIDs, writable disk)",
        ["3.3", "3.6", "3.8", "3.9", "3.10"],
        [_D_ISOLATION, (f"{_INFRA}/test_docker_spec_properties.py", _p(1))],
    ),
    (
        "security: all capabilities dropped, no-new-privileges, unprivileged",
        ["3.4", "3.5", "3.10"],
        [_D_ISOLATION, (f"{_INFRA}/test_docker_spec.py", _T)],
    ),
    (
        "security: no learner code in the api or worker process",
        ["5.1", "5.2", "5.3", "5.4", "5.5"],
        [_D_CANARY],
    ),
    (
        "security: grading isolation (read-only root-owned bundle, Grader_User, removed after)",
        ["10.1", "10.2", "10.3", "10.4", "10.5", "10.6", "10.10", "10.12"],
        [_D_GRADING, (f"{_APP}/test_grade_run_independence_properties.py", _p(26))],
    ),
    (
        "security: fail-closed freeze of learner processes before any Check",
        ["10.7", "10.8", "10.11", "28.8"],
        [_D_FREEZE, (f"{_APP}/test_grade_run_freeze_property.py", _p(35))],
    ),
    (
        "security: tamper resistance (files, PATH, aliases, environment, special files)",
        ["14.12", "29.1", "29.2", "29.3", "29.4", "29.5", "29.9", "29.10", "29.11"],
        [_D_TAMPER_1, _D_TAMPER_2, _D_HARNESS, _D_UNSAFE],
    ),
    (
        "security: throttles (one active session, provisioning and grading rate limits)",
        ["7.1", "7.4", "7.5", "30.1", "30.3", "30.4"],
        [
            (f"{_LABS}/test_one_active_session_property.py", _p(3)),
            (f"{_LABS}/test_grading_acceptance_property.py", _p(6)),
            (f"{_APP}/test_request_grading_sequences_properties.py", _p(7)),
            (f"{_DOM}/test_lab_property_provision_rate.py", _p(9)),
            (f"{_API}/test_labs_second_session_failures.py", _T),
        ],
    ),
    (
        "security: the Docker provider is dev only and refused in production",
        ["2.1", "2.2", "2.3"],
        [
            (f"{_INFRA}/test_sandbox_provider_selection.py", _T),
            (f"{_INFRA}/test_docker_spec.py", _T),
        ],
    ),
    # --- .kiro/steering/labs.md, Kubernetes Sandbox Provider (ADR 0009) -------------------
    (
        "Exec, copy and terminal use the Kubernetes `pods/exec`",
        [f"{K8S_PREFIX}1.8", f"{K8S_PREFIX}4.7", f"{K8S_PREFIX}5.1"],
        [(f"{_INFRA}/test_k8s_exec.py", _T), _K_SPIKE_RBAC],
    ),
    (
        "Worker RBAC: one `Role` in `labs`",
        [f"{K8S_PREFIX}4.1", f"{K8S_PREFIX}4.7"],
        [_K_SPIKE_RBAC],
    ),
    (
        "Api exec admission policy",
        [f"{K8S_PREFIX}4.7"],
        [(_K_SPIKE_FILE, "ValidatingAdmissionPolicy"), _K_SPIKE_RBAC],
    ),
    (
        "Pod layout B is chosen",
        [f"{K8S_PREFIX}2.2", f"{K8S_PREFIX}2.6", f"{K8S_PREFIX}5.1", f"{K8S_PREFIX}5.4"],
        [
            (f"{_INFRA}/test_k8s_pod_spec.py", "runAsUser"),
            (f"{_INFRA}/test_k8s_golden.py", _T),
            (f"{_INFRA}/test_k8s_pod_spec_properties.py", "kubernetes-deployment, Property 2:"),
            (_K_SPIKE_FILE, "def check_isolation"),
        ],
    ),
    (
        "Reaping init requirement",
        [f"{K8S_PREFIX}5.4", f"{K8S_PREFIX}5.6"],
        [
            (f"{_INFRA}/test_k8s_pod_spec.py", "test_every_container_init_reaps_and_never_forks"),
            (_K_SPIKE_FILE, "def check_freeze"),
            (f"{_K8S}/test_provider_suites_kind.py", "test_lab_suite_passes_with_the_kubernetes"),
        ],
    ),
    (
        "The `grader` container, where every Check runs",
        [f"{K8S_PREFIX}2.3", f"{K8S_PREFIX}5.7"],
        [
            (f"{_INFRA}/test_k8s_pod_spec_properties.py", "K8S_GRADER_CPU_MILLI"),
            (f"{_LABS}/test_docker_check_abuse.py", "def test_abusive_check_is_contained"),
        ],
    ),
    (
        "The exec wrapper clears the environment",
        [f"{K8S_PREFIX}5.6", f"{K8S_PREFIX}1.11"],
        [
            (_K_EXEC_TT, "def test_timeout_runs_the_command_under"),
            (_K_EXEC_TT, "def test_exec_start_failure_is_retried"),
            (f"{_LABS}/fixtures/grading_isolation_bundle/sandbox/checks/c_env.sh", "EDU_IT_EXEC"),
        ],
    ),
    (
        "The freeze of learner processes runs inside",
        [f"{K8S_PREFIX}5.4"],
        [
            (_K_SPIKE_FILE, "def check_freeze"),
            (f"{_INFRA}/test_k8s_exec.py", "test_exec_runs_in_the_container_of_as_user"),
            (f"{_APP}/test_grade_run_freeze_property.py", _p(35)),
            (f"{_APP}/test_grade_run_freeze_namespaces.py", "test_unstoppable_learner_process"),
            (f"{_K8S}/test_provider_suites_kind.py", "test_freeze_verification_sees_real"),
        ],
    ),
    (
        "Memory containment on Kubernetes needs the kubelet setting",
        [f"{K8S_PREFIX}2.4", f"{K8S_PREFIX}16.10"],
        [
            (f"{_K8S}/test_provider_suites_kind.py", "single_process_oom_kill"),
            (f"{_LABS}/test_docker_isolation.py", "test_memory_loop_is_contained"),
        ],
    ),
    (
        "Network containment on Kubernetes is the default-deny NetworkPolicy",
        [f"{K8S_PREFIX}3.1", f"{K8S_PREFIX}16.10"],
        [
            (f"{_K8S}/conftest.py", "def labs_default_deny_policy"),
            (f"{_LABS}/test_docker_isolation.py", "test_outbound_connect_fails"),
        ],
    ),
    (
        "Exec timeout: every `exec`",
        [f"{K8S_PREFIX}5.6", f"{K8S_PREFIX}1.12"],
        [
            (f"{_INFRA}/test_k8s_exec_timeout_and_terminal.py", _T),
            (f"{_INFRA}/test_k8s_exec.py", "test_timeout_kills_the_marked_processes"),
        ],
    ),
    (
        "Copy is a tar stream over `exec`",
        [f"{K8S_PREFIX}5.1", f"{K8S_PREFIX}5.2"],
        [(f"{_INFRA}/test_k8s_exec.py", "# Claims: 5.2")],
    ),
    (
        "The terminal is `/bin/bash -l`",
        [f"{K8S_PREFIX}1.10", f"{K8S_PREFIX}1.11", f"{K8S_PREFIX}1.12"],
        [
            (f"{_INFRA}/test_k8s_exec.py", "test_terminal_operations_raise_gone_after_pod_loss"),
            (f"{_INFRA}/test_k8s_exec_timeout_and_terminal.py", _T),
        ],
    ),
    (
        "The pod name is deterministic",
        [f"{K8S_PREFIX}1.4", f"{K8S_PREFIX}1.6", f"{K8S_PREFIX}20.5"],
        [
            (f"{_INFRA}/test_k8s_pod_identity_properties.py", "kubernetes-deployment, Property 3:"),
            (f"{_INFRA}/test_k8s_provider_lifecycle.py", "test_409_with_same_session_label"),
            (f"{_INFRA}/test_property_k8s_provision_idempotent.py", _T),
        ],
    ),
    (
        "The provider waits `min(LAB_PROVISION_TIMEOUT",
        [f"{K8S_PREFIX}1.5", f"{K8S_PREFIX}6.2"],
        [
            (f"{_INFRA}/test_k8s_provider_lifecycle.py", "test_wait_uses_min_of_provision"),
            (f"{_INFRA}/test_property_k8s_provision_failure.py", _T),
        ],
    ),
    (
        "Destroy deletes the pod and waits",
        [f"{K8S_PREFIX}6.3", f"{K8S_PREFIX}6.4", f"{K8S_PREFIX}6.5"],
        [(f"{_INFRA}/test_property_k8s_destroy.py", _T)],
    ),
    (
        "The PID limit is the pod annotation",
        [f"{K8S_PREFIX}2.6"],
        [
            (f"{_INFRA}/test_k8s_pod_spec_properties.py", "ANNOTATION_PID_LIMIT"),
            (f"{_INFRA}/test_k8s_golden.py", _T),
        ],
    ),
    (
        "Quota rejection is the distinct domain error",
        [f"{K8S_PREFIX}2.11", f"{K8S_PREFIX}22.1"],
        [
            (f"{_INFRA}/test_k8s_errors.py", "test_capacity_error_is_a_provision_error"),
            (f"{_INFRA}/test_k8s_create_rejection_properties.py", _T),
        ],
    ),
    (
        "Backend selection: `SANDBOX_PROVIDER=kubernetes`",
        [f"{K8S_PREFIX}1.2", f"{K8S_PREFIX}1.3"],
        [
            (f"{_INFRA}/test_sandbox_provider_selection.py", "test_kubernetes_without_a_client"),
            (f"{_INFRA}/test_property_sandbox_backend_parsing.py", "Property 1:"),
        ],
    ),
    (
        "Evidence status: the Sandbox_Port contract suite",
        [f"{K8S_PREFIX}1.9", f"{K8S_PREFIX}5.7"],
        [
            (f"{_INFRA}/test_sandbox_provider_contract.py", '"kubernetes"'),
            (f"{_LABS}/test_sandbox_backend.py", "# Claims: 1.9, 5.7"),
        ],
    ),
]

SECURITY_PREFIX = "security: "


# --- Rule extraction ------------------------------------------------------------------------


def _bullets(lines: Sequence[str]) -> list[str]:
    """Bullet lines outside code fences; a bullet ending in ':' only introduces a nested list."""
    out: list[str] = []
    fenced = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and re.match(r"^\s*- ", line) and not line.rstrip().endswith(":"):
            out.append(line.strip()[2:])
    return out


def _section(text: str, start: str, end: str | None) -> list[str]:
    lines = text.splitlines()
    begin = next(i for i, line in enumerate(lines) if line.strip() == start)
    stop = len(lines)
    if end is not None:
        stop = next(i for i, line in enumerate(lines) if i > begin and line.strip() == end)
    return lines[begin + 1 : stop]


def labs_rule_bullets() -> list[str]:
    """Every rule bullet of labs.md, the Labs section of domain-rules.md, and LABS-DESIGN s.4."""
    steering = LABS_STEERING.read_text(encoding="utf-8").splitlines()
    domain = _section(DOMAIN_RULES.read_text(encoding="utf-8"), "## Labs", "## Projects")
    design = _section(
        LABS_DESIGN.read_text(encoding="utf-8"),
        "## Failure Scenarios",
        "## Related Steering and Documents",
    )
    return _bullets(steering) + _bullets(domain) + _bullets(design)


def criteria_ids(path: Path = REQUIREMENTS) -> set[str]:
    """Requirement numbers ``N`` and criterion ids ``N.k`` declared in requirements.md.

    With the default path the ids of the kubernetes-deployment spec are added too, each prefixed
    with ``K8S_PREFIX``; an unprefixed id is always a hands-on-labs id.
    """
    ids = _criteria_ids(path)
    if path == REQUIREMENTS:
        ids |= {K8S_PREFIX + i for i in _criteria_ids(K8S_REQUIREMENTS)}
    return ids


def _criteria_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^### Requirement (\d+):", line)
        if heading:
            current = heading.group(1)
            ids.add(current)
            continue
        item = re.match(r"^(\d+)\. ", line)
        if item and current:
            ids.add(f"{current}.{item.group(1)}")
    return ids


def _has_docker_marker(path: Path) -> bool:
    return "mark.docker" in path.read_text(encoding="utf-8")


# --- The traceability check (also exercised with broken fixtures, task 22.2) ---------------


def check_traceability(
    rule_map: RuleMap,
    bullets: Sequence[str],
    known_ids: set[str],
    repo: Path = REPO,
) -> list[str]:
    """Every problem of the mapping; empty when every rule traces to requirements and tests."""
    problems: list[str] = []
    for bullet in bullets:
        entries = [e for e in rule_map if e[0] in bullet]
        if not entries:
            problems.append(f"rule has no requirement/test mapping: {bullet!r}")
    for key, requirements, tests in rule_map:
        if not key.startswith(SECURITY_PREFIX) and not any(key in b for b in bullets):
            problems.append(f"stale mapping for {key!r}")
        reqs = [requirements] if isinstance(requirements, str) else list(requirements)
        if not reqs:
            problems.append(f"rule {key!r} maps to no requirement")
        for requirement in reqs:
            if requirement not in known_ids:
                problems.append(f"unknown requirement {requirement} for {key!r}")
        if not tests:
            problems.append(f"rule {key!r} maps to no test")
        for test_file, needle in tests:
            path = repo / test_file
            if not path.is_file():
                problems.append(f"missing test file {test_file} for {key!r}")
            elif needle not in path.read_text(encoding="utf-8"):
                problems.append(f"needle {needle!r} not in {test_file} for {key!r}")
        if key.startswith(SECURITY_PREFIX) and key not in DOCKER_EVIDENCE_EXEMPT:
            docker = [f for f, _ in tests if (repo / f).is_file() and _has_docker_marker(repo / f)]
            if not docker:
                problems.append(f"security rule {key!r} maps to no docker-marked test")
    return problems


def evidence_classes_documented(source: str) -> bool:
    """True when the suite documentation states the evidence classes (Requirements 10.12, 29.11)."""
    flat = " ".join(source.replace("#", " ").replace('"', " ").split()).lower()
    required = (
        "Fake_Provider tests verify call logic only and are not security evidence",
        "tamper resistance is evidenced by the Docker_Provider tests",
        "security and grading-integrity rules map to the Docker tests",
    )
    return all(phrase.lower() in flat for phrase in required)


# --- Tests ---------------------------------------------------------------------------------


def test_every_labs_rule_maps_to_a_requirement_and_a_test() -> None:
    bullets = labs_rule_bullets()
    assert len(bullets) >= 30, "rule extraction found too few bullets"
    problems = check_traceability(RULE_MAP, bullets, criteria_ids())
    assert problems == []


def test_grading_is_bounded_is_remapped_to_requirements_7_4_11_and_28() -> None:
    entries = [e for e in RULE_MAP if e[0] == "Grading is bounded"]
    assert len(entries) == 1
    assert entries[0][1] == ["7.4", "11", "28"]
    assert entries[0][2]


def test_rule_map_has_the_phase_2_shape_phase_report_expects() -> None:
    for key, requirements, tests in RULE_MAP:
        assert isinstance(key, str)
        assert isinstance(requirements, (str, list))
        assert all(
            isinstance(r, str)
            for r in ([requirements] if isinstance(requirements, str) else requirements)
        )
        assert all(isinstance(f, str) and isinstance(n, str) for f, n in tests)


def test_security_rules_cover_every_listed_integrity_rule() -> None:
    keys = " | ".join(k for k, _, _ in RULE_MAP if k.startswith(SECURITY_PREFIX))
    for fragment in (
        "non-root",
        "no network",
        "four resource limits",
        "capabilities",
        "no learner code in the api or worker",
        "grading isolation",
        "fail-closed freeze",
        "tamper resistance",
        "throttles",
        "dev only",
    ):
        assert fragment in keys, f"no security rule for {fragment!r}"


def test_evidence_classes_are_stated_in_the_suite_documentation() -> None:
    assert evidence_classes_documented(THIS_FILE.read_text(encoding="utf-8"))
    source = THIS_FILE.read_text(encoding="utf-8")
    # The module docstring and the comment header directly above RULE_MAP each state it.
    docstring = source.split('"""', 2)[1]
    header = source.split("RULE_MAP: list", 1)[0].rsplit("# Evidence classes", 1)[1]
    assert evidence_classes_documented(docstring)
    assert evidence_classes_documented(header)
    assert evidence_classes_documented(EVIDENCE_CLASSES)


# --- Negative fixtures (task 22.2): the check must fail on a broken mapping ------------------

_OK_TEST = "tests/labs/test_grading_acceptance_property.py"


@pytest.fixture
def known() -> set[str]:
    return criteria_ids()


def test_check_passes_on_a_valid_fixture(known: set[str]) -> None:
    rule_map = [("Rule A", ["7.4"], [(_OK_TEST, _p(6))])]
    assert check_traceability(rule_map, ["Rule A is stated"], known) == []


def test_check_fails_on_an_unmapped_rule(known: set[str]) -> None:
    rule_map = [("Rule A", ["7.4"], [(_OK_TEST, _p(6))])]
    problems = check_traceability(rule_map, ["Rule A is stated", "Rule B is stated"], known)
    assert any("Rule B" in p and "no requirement/test mapping" in p for p in problems)


def test_check_fails_on_a_rule_without_requirement_or_test(known: set[str]) -> None:
    no_req = check_traceability([("Rule A", [], [(_OK_TEST, _p(6))])], ["Rule A"], known)
    assert any("no requirement" in p for p in no_req)
    no_test = check_traceability([("Rule A", ["7.4"], [])], ["Rule A"], known)
    assert any("no test" in p for p in no_test)
    unknown = check_traceability([("Rule A", ["999.9"], [(_OK_TEST, _p(6))])], ["Rule A"], known)
    assert any("unknown requirement" in p for p in unknown)


def test_check_fails_on_a_missing_test_file(known: set[str]) -> None:
    rule_map = [("Rule A", ["7.4"], [("tests/labs/test_does_not_exist.py", _T)])]
    problems = check_traceability(rule_map, ["Rule A"], known)
    assert any("missing test file" in p for p in problems)


def test_check_fails_on_a_missing_needle(known: set[str]) -> None:
    rule_map = [("Rule A", ["7.4"], [(_OK_TEST, "no such needle anywhere 0xDEADBEEF")])]
    problems = check_traceability(rule_map, ["Rule A"], known)
    assert any("needle" in p and "not in" in p for p in problems)


def test_check_fails_on_a_stale_entry(known: set[str]) -> None:
    rule_map = [("Rule A", ["7.4"], [(_OK_TEST, _p(6))]), ("Gone", ["7.4"], [(_OK_TEST, _p(6))])]
    assert any("stale mapping" in p for p in check_traceability(rule_map, ["Rule A"], known))


def test_check_fails_on_a_security_rule_without_docker_evidence(known: set[str]) -> None:
    fake_only = (f"{_APP}/test_grade_run_independence_properties.py", _p(26))
    rule_map = [(f"{SECURITY_PREFIX}grading isolation", ["10.1"], [fake_only])]
    problems = check_traceability(rule_map, [], known)
    assert any("no docker-marked test" in p for p in problems)


def test_evidence_class_statement_check_detects_its_absence() -> None:
    assert evidence_classes_documented(__doc__ or "")
    assert not evidence_classes_documented('"""A suite with no evidence classes."""')
