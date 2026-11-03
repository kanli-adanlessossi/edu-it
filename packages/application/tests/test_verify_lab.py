"""Unit tests for VerifyLab (ai-content task 7.2).

**Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from edu_it_application.ai import VerifyLab
from edu_it_domain import config
from edu_it_domain.ai.verification import (
    FindingKind,
    LabCheckUnderVerification,
    LabUnderVerification,
)
from edu_it_domain.ports.sandbox import ExecResult, SandboxGone
from edu_it_infrastructure.fakes import Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox, ProvisionMode

_SOLVE = "SOLVE"
_CHECK = "CHECK"


def _lab(*checks: LabCheckUnderVerification, setup: str | None = None) -> LabUnderVerification:
    return LabUnderVerification(
        lab_id="lab1",
        image="img",
        checks=checks or (LabCheckUnderVerification("c1", _CHECK, 5.0),),
        reference_solution=_SOLVE,
        setup_script=setup,
    )


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.sandbox.exec_handler = self._handler
        self.verify = VerifyLab(sandbox=self.sandbox, clock=self.clock)

    def _handler(self, sb: FakeSandbox, command: Sequence[str], as_user: str) -> ExecResult | None:
        script = command[2]
        if script == _SOLVE:
            sb.files["/solved"] = b"1"
            return ExecResult(0, b"", b"", False)
        if script.startswith(_CHECK):
            return ExecResult(0 if "/solved" in sb.files else 1, b"", b"", False)
        return None


def test_correct_lab_has_no_findings_and_destroys_both_sandboxes() -> None:
    env = _Env()
    assert env.verify.execute(_lab()) == ()
    assert env.sandbox.list_sandboxes() == ()
    assert env.sandbox.calls.count("provision") == 2
    assert env.sandbox.calls.count("destroy") == 2


def test_check_passing_on_starting_state_is_a_finding() -> None:
    env = _Env()
    env.sandbox.exec_handler = lambda sb, cmd, user: (
        ExecResult(0, b"", b"", False) if cmd[2].startswith(_CHECK) else None
    )
    findings = env.verify.execute(_lab())
    assert [(f.kind, f.target) for f in findings] == [
        (FindingKind.CHECK_PASSES_ON_STARTING_STATE, "c1")
    ]


def test_check_failing_on_reference_solution_is_a_finding() -> None:
    env = _Env()
    env.sandbox.exec_handler = lambda sb, cmd, user: (
        ExecResult(0, b"", b"", False)
        if cmd[2] == _SOLVE
        else ExecResult(1, b"", b"", False)
        if cmd[2].startswith(_CHECK)
        else None
    )
    findings = env.verify.execute(_lab())
    assert [f.kind for f in findings] == [FindingKind.CHECK_FAILS_ON_REFERENCE_SOLUTION]


def test_provisioning_failure_is_a_finding_and_nothing_leaks() -> None:
    env = _Env()
    env.sandbox.script_provision(ProvisionMode.OK, ProvisionMode.FAIL)
    findings = env.verify.execute(_lab())
    assert [f.kind for f in findings] == [FindingKind.SANDBOX_UNAVAILABLE]
    assert env.sandbox.list_sandboxes() == ()


def test_exec_failure_is_a_finding_and_both_sandboxes_are_destroyed() -> None:
    env = _Env()

    def boom(sb: FakeSandbox, cmd: Sequence[str], user: str) -> ExecResult | None:
        raise SandboxGone("gone")

    env.sandbox.exec_handler = boom
    findings = env.verify.execute(_lab())
    assert [f.kind for f in findings] == [FindingKind.SANDBOX_UNAVAILABLE] * 2
    assert env.sandbox.list_sandboxes() == ()


def test_failed_setup_is_a_finding_not_a_pass() -> None:
    env = _Env()
    env.sandbox.exec_handler = lambda sb, cmd, user: (
        ExecResult(2, b"", b"", False) if cmd[2] == "SETUP" else None
    )
    findings = env.verify.execute(_lab(setup="SETUP"))
    assert [f.kind for f in findings] == [FindingKind.SETUP_FAILED] * 2


def test_timeout_is_inconclusive_and_bounds_never_exceed_usable_budget() -> None:
    env = _Env()
    seen: list[float | None] = []
    real_exec = env.sandbox.exec

    def spy(handle, command, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs["timeout_s"])
        return real_exec(handle, command, **kwargs)

    env.sandbox.exec = spy  # type: ignore[method-assign]
    env.sandbox.exec_handler = lambda sb, cmd, user: (
        ExecResult(137, b"", b"", True) if cmd[2].startswith(_CHECK) else None
    )
    long_check = LabCheckUnderVerification("c1", _CHECK, 600.0)
    findings = env.verify.execute(_lab(long_check))
    assert [f.kind for f in findings] == [FindingKind.CHECK_INCONCLUSIVE] * 2
    assert seen
    assert all(t is not None and t <= config.USABLE_RUN_BUDGET.total_seconds() for t in seen)


def test_phase_deadline_stops_remaining_checks_with_findings() -> None:
    env = _Env()
    env.sandbox.call_duration = config.LAB_GRADING_RUN_DEADLINE + timedelta(seconds=1)
    findings = env.verify.execute(
        _lab(
            LabCheckUnderVerification("c1", _CHECK, 5.0),
            LabCheckUnderVerification("c2", _CHECK, 5.0),
        )
    )
    assert FindingKind.PHASE_DEADLINE_EXCEEDED in {f.kind for f in findings}
    assert env.sandbox.list_sandboxes() == ()
