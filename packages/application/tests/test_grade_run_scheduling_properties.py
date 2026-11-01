"""Property 11 for GradeRun (task 6.11).

Feature: hands-on-labs, Property 11: Grading run scheduling and aggregation

Fake_SandboxProvider, in-memory repositories and Test_Clock only. Check execution time is simulated
by advancing the Test_Clock inside the provider's ``exec``; the freeze commands also cost virtual
time, which must come out of the freeze bound and never out of the Usable_Run_Budget.

**Validates: Requirements 11.2, 11.3, 11.4, 11.7, 11.8, 11.9, 27.4, 28.1, 28.3, 28.4, 28.5**
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_application.labs import GradeRun, RecordCheckResult
from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckOutcome,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_USABLE_MS = int(
    (
        config.LAB_GRADING_RUN_DEADLINE
        - config.LAB_GRADING_FREEZE_BOUND
        - config.LAB_GRADING_TEARDOWN_RESERVE
    ).total_seconds()
    * 1000
)
_DEADLINE_MS = int(config.LAB_GRADING_RUN_DEADLINE.total_seconds() * 1000)
_FREEZE_CALL_MS = 200  # each freeze exec; one attempt = 2 execs = 400 ms < the 1 s bound
_STOP = ["/bin/kill", "-s", "STOP", "-1"]
_PS = "/bin/ps"


@dataclass(frozen=True)
class _Spec:
    timeout_ms: int
    duration_ms: int  # simulated run time if it were not cut off
    exit_code: int  # 0 pass, 1 fail, other = could not complete


_spec = st.builds(
    _Spec,
    timeout_ms=st.integers(100, 30_000),
    duration_ms=st.integers(0, 40_000),
    exit_code=st.sampled_from([0, 0, 1, 2]),
)
_checks = st.lists(_spec, min_size=1, max_size=8)
# Queue delay from acceptance to job start: well inside, at, and beyond the Usable_Run_Budget.
_queue_ms = st.one_of(st.integers(0, _USABLE_MS + 3_000), st.sampled_from([0, _USABLE_MS]))


def _lab(specs: list[_Spec]) -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind="terminal",
        image="img",
        concept_ids=("cn1",),
        tasks=tuple(LabTaskDefinition(f"t{i}", required=True) for i in range(len(specs))),
        checks=tuple(
            LabCheckDefinition(f"c{i}", f"t{i}", s.timeout_ms / 1000, {"pass": "fb.ok"})
            for i, s in enumerate(specs)
        ),
        bundle_path="lab1",
    )


@dataclass
class _Outcome:
    status: RunStatus | None
    outcomes: dict[str, CheckOutcome]
    seen_timeouts: dict[str, float]
    executed_ms: int
    pre_teardown_end_ms: int  # ms from acceptance to the instant the last Check finished
    calls: list[str]
    mastery_granted: bool
    session_ready: bool
    count: int


def _grade(specs: list[_Spec], queue_ms: int, vanish_at: int | None = None) -> _Outcome:
    lab = _lab(specs)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (root / "lab1" / "expected").mkdir()
        clock = Test_Clock()
        runs = In_Memory_GradingRunRepository()
        sessions = In_Memory_LabSessionRepository(runs)
        mastery = In_Memory_MasteryRepository()
        sandbox = Fake_SandboxProvider(clock)
        handle = sandbox.provision(build_sandbox_spec(lab, "s1"))
        sandbox.add_process(handle, "learner", "bash")
        accepted = clock.now()
        sessions.add_if_no_active(
            LabSession(
                "s1",
                "u1",
                "lab1",
                SessionStatus.GRADING,
                accepted,
                accepted + config.LAB_SESSION_TTL,
            )
        )
        sessions.transition(
            "s1",
            expected={SessionStatus.GRADING},
            to=SessionStatus.GRADING,
            sandbox_ref=handle.sandbox_id,
        )
        runs.add(
            GradingRun(
                "r1",
                "s1",
                RunStatus.RUNNING,
                accepted,
                accepted + config.LAB_GRADING_RUN_DEADLINE,
            )
        )
        seen: dict[str, float] = {}
        executed = [0]
        last_check_end = [0]
        real_exec = sandbox.exec

        def spy(handle: SandboxHandle, command: Sequence[str], **kw: Any) -> ExecResult:
            cmd = list(command)
            if cmd[:1] == ["/bin/sh"]:
                idx = int(cmd[1].rsplit("/c", 1)[1].removesuffix(".sh"))
                if vanish_at == idx:
                    sandbox.vanish(handle)
                    return real_exec(handle, command, **kw)  # raises SandboxGone
                spec = specs[idx]
                limit_ms = round(kw["timeout_s"] * 1000)
                seen[f"c{idx}"] = kw["timeout_s"]
                timed_out = spec.duration_ms >= limit_ms
                ran = limit_ms if timed_out else spec.duration_ms
                sandbox.calls.append("exec")
                clock.advance(timedelta(milliseconds=ran))
                executed[0] += ran
                last_check_end[0] = round((clock.now() - accepted).total_seconds() * 1000)
                return ExecResult(-1 if timed_out else spec.exit_code, b"", b"", timed_out)
            if cmd == _STOP or cmd[:1] == [_PS]:
                clock.advance(timedelta(milliseconds=_FREEZE_CALL_MS))
            return real_exec(handle, command, **kw)

        sandbox.exec = spy  # type: ignore[method-assign]
        clock.advance(timedelta(milliseconds=queue_ms))  # queue delay after acceptance
        job = GradeRun(
            sessions=sessions,
            runs=runs,
            catalog=Fake_LabCatalog([lab]),
            sandbox=sandbox,
            record=RecordCheckResult(runs=runs, mastery=mastery, clock=clock),
            clock=clock,
            bundle_root=root,
        )
        status = job.execute("r1")
        run = runs.get("r1")
        session = sessions.get("s1")
        assert run is not None and session is not None
        return _Outcome(
            status=status,
            outcomes={r.check_id: r.outcome for r in run.results},
            seen_timeouts=seen,
            executed_ms=executed[0],
            pre_teardown_end_ms=last_check_end[0],
            calls=list(sandbox.calls),
            mastery_granted=mastery.get("u1", "cn1") is not None,
            session_ready=session.status is SessionStatus.READY,
            count=len(run.results),
        )


def _expected_aggregate(outcomes: list[CheckOutcome]) -> RunStatus:
    if all(o is CheckOutcome.PASSED for o in outcomes):
        return RunStatus.PASSED
    return RunStatus.FAILED


# Feature: hands-on-labs, Property 11: Grading run scheduling and aggregation
@settings(max_examples=150, deadline=None)
@given(specs=_checks, queue_ms=_queue_ms)
def test_property_11_grading_run_scheduling_and_aggregation(
    specs: list[_Spec], queue_ms: int
) -> None:
    out = _grade(specs, queue_ms)
    remaining_ms = _USABLE_MS - queue_ms  # budget measured from the accepted instant (11.8)

    # Every Check has exactly one recorded result and the session always returns to ready.
    assert out.count == len(specs)
    assert out.session_ready

    if remaining_ms <= 0:
        # No budget: nothing frozen, copied or executed; every Check could_not_complete.
        assert out.status is RunStatus.COULD_NOT_COMPLETE
        assert set(out.outcomes.values()) == {CheckOutcome.COULD_NOT_COMPLETE}
        assert "copy_in" not in out.calls
        assert not out.seen_timeouts
        assert not out.mastery_granted
        return

    # Model the schedule: each Check gets min(own timeout, what is left of the budget).
    left = remaining_ms
    expected: list[CheckOutcome] = []
    for i, s in enumerate(specs):
        timeout = min(s.timeout_ms, left)
        if timeout <= 0:
            expected.append(CheckOutcome.COULD_NOT_COMPLETE)  # not reached: no budget left
            assert f"c{i}" not in out.seen_timeouts
            continue
        # effective_timeout per Check: never above own timeout nor the remaining budget.
        assert round(out.seen_timeouts[f"c{i}"] * 1000) == timeout
        if s.duration_ms >= timeout:  # timed out; later Checks still run while budget remains
            expected.append(CheckOutcome.COULD_NOT_COMPLETE)
            left -= timeout
        else:
            left -= s.duration_ms
            expected.append(
                {0: CheckOutcome.PASSED, 1: CheckOutcome.FAILED}.get(
                    s.exit_code, CheckOutcome.COULD_NOT_COMPLETE
                )
            )
    assert [out.outcomes[f"c{i}"] for i in range(len(specs))] == expected

    # Executed durations never exceed the Usable_Run_Budget minus the queue delay, and the
    # whole run (queue + freeze + executed + teardown reserve) stays inside the 15 s deadline.
    assert out.executed_ms <= remaining_ms
    reserve_ms = int(config.LAB_GRADING_TEARDOWN_RESERVE.total_seconds() * 1000)
    assert out.pre_teardown_end_ms + reserve_ms <= _DEADLINE_MS

    # The freeze cost was not charged to the budget: Checks got the full remaining budget.
    first_run = next((i for i in range(len(specs)) if f"c{i}" in out.seen_timeouts), None)
    if first_run == 0:
        assert round(out.seen_timeouts["c0"] * 1000) == min(specs[0].timeout_ms, remaining_ms)

    # Aggregation: passed iff every Check passed; otherwise failed.
    assert out.status is _expected_aggregate(expected)
    assert out.mastery_granted == (CheckOutcome.PASSED in expected)


# Feature: hands-on-labs, Property 11: Grading run scheduling and aggregation
@settings(max_examples=100, deadline=None)
@given(
    # Short Checks (<= 1 s each, <= 8 of them) so the vanishing Check is always reached.
    specs=st.lists(
        st.builds(
            _Spec,
            timeout_ms=st.integers(100, 1_000),
            duration_ms=st.integers(0, 1_500),
            exit_code=st.sampled_from([0, 1, 2]),
        ),
        min_size=1,
        max_size=8,
    ),
    data=st.data(),
)
def test_property_11_vanished_sandbox_is_could_not_complete(
    specs: list[_Spec], data: st.DataObject
) -> None:
    vanish_at = data.draw(st.integers(0, len(specs) - 1))
    out = _grade(specs, 0, vanish_at=vanish_at)
    assert out.status is RunStatus.COULD_NOT_COMPLETE
    assert out.count == len(specs)
    for i in range(vanish_at, len(specs)):
        assert out.outcomes[f"c{i}"] is CheckOutcome.COULD_NOT_COMPLETE
    # No mastery for unfinished tasks: any mastery must come from a Check that finished passing.
    earlier_passed = any(out.outcomes[f"c{i}"] is CheckOutcome.PASSED for i in range(vanish_at))
    assert out.mastery_granted == earlier_passed
    assert out.session_ready
