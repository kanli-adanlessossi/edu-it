"""Property test for GradeRun's fail-closed freeze (task 6.19).

Feature: hands-on-labs, Property 35: Freeze failure is fail-closed

**Validates: Requirements 10.7, 10.8, 10.11, 28.8**
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

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
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

_STOP = ["/bin/kill", "-s", "STOP", "-1"]
_CONT = ["/bin/kill", "-s", "CONT", "-1"]
_PS = "/bin/ps"
_CHECK_ARGV = {"c1": ["/bin/sh", "/grader/checks/c1.sh"], "c2": ["/bin/sh", "/grader/checks/c2.sh"]}
_CHECK_IDS = ("c1", "c2")

_ATTEMPTS = config.LAB_GRADING_FREEZE_ATTEMPTS
_MODES = ("stop_exit", "exec_error", "ps_running", "ps_exit")

# ("verified", k, mode): attempts 1..k-1 fail with `mode`, attempt k verifies.
# ("never", mode): every attempt fails with `mode`.
# ("slow", seconds): each call advances the fake Clock past the 1 s bound.
_FREEZE = st.one_of(
    st.tuples(st.just("verified"), st.integers(1, _ATTEMPTS), st.sampled_from(_MODES)),
    st.tuples(st.just("never"), st.sampled_from(_MODES)),
    st.tuples(st.just("slow"), st.sampled_from([0.6, 1.0, 2.0, 5.0])),
)
# GradeRun outcomes. The second element is the Check index where relevant.
_OUTCOME = st.one_of(
    st.just(("success", 0)),
    st.just(("budget", 0)),
    st.just(("exc_copy_in", 0)),
    st.tuples(st.just("exc_check"), st.integers(0, 1)),
    st.tuples(st.just("vanish_check"), st.integers(0, 1)),
)


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind="terminal",
        image="img",
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True),),
        checks=(
            LabCheckDefinition("c1", "t1", 5.0, {"pass": "fb.ok"}),
            LabCheckDefinition("c2", "t1", 5.0),
        ),
        bundle_path="lab1",
    )


class FreezeFailClosed(RuleBasedStateMachine):
    """Drives one GradeRun under a generated freeze scenario and run outcome."""

    @initialize(freeze=_FREEZE, outcome=_OUTCOME)
    def setup(self, freeze: tuple[Any, ...], outcome: tuple[str, int]) -> None:
        self.freeze = freeze
        self.outcome = outcome
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (self.tmp / "lab1" / "sandbox" / "checks" / "c1.sh").write_text("exit 0\n")
        (self.tmp / "lab1" / "expected").mkdir()
        self.lab = _lab()
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.mastery = In_Memory_MasteryRepository()
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.handle = self.sandbox.provision(build_sandbox_spec(self.lab, "s1"))
        self.sandbox.add_process(self.handle, "learner", "bash")
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession("s1", "u1", "lab1", SessionStatus.GRADING, now, now + config.LAB_SESSION_TTL)
        )
        self.sessions.transition(
            "s1",
            expected={SessionStatus.GRADING},
            to=SessionStatus.GRADING,
            sandbox_ref=self.handle.sandbox_id,
        )
        self.runs.add(
            GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
        )
        self.job = GradeRun(
            sessions=self.sessions,
            runs=self.runs,
            catalog=Fake_LabCatalog([self.lab]),
            sandbox=self.sandbox,
            record=RecordCheckResult(runs=self.runs, mastery=self.mastery, clock=self.clock),
            clock=self.clock,
            bundle_root=self.tmp,
        )
        self.commands: list[tuple[list[str], str]] = []
        self.violations: list[str] = []
        self.stops = 0
        self.ran = False
        self.status: RunStatus | None = None
        self._install()

    # -- scripting -----------------------------------------------------------

    def _fail_mode(self) -> str | None:
        """Failure mode to apply to the current freeze attempt (``None`` = behave normally)."""
        kind = self.freeze[0]
        if kind == "never":
            return cast("str | None", self.freeze[1])
        if kind == "verified" and self.stops < self.freeze[1]:
            return cast("str | None", self.freeze[2])
        return None

    def _install(self) -> None:
        kind = self.freeze[0]
        if kind == "slow":
            self.sandbox.call_duration = timedelta(seconds=self.freeze[1])
        orig_copy_in = self.sandbox.copy_in

        def copy_in(handle, source, dest, *, as_user=None):  # type: ignore[no-untyped-def]
            self.commands.append((["<copy_in>"], as_user or ""))
            self._assert_frozen("copy_in")
            if self.outcome[0] == "exc_copy_in":
                raise RuntimeError("copy_in failed")
            orig_copy_in(handle, source, dest, as_user=as_user)

        self.sandbox.copy_in = copy_in  # type: ignore[method-assign]

        def handler(sb, command, as_user):  # type: ignore[no-untyped-def]
            command = list(command)
            self.commands.append((command, as_user))
            if command == _STOP:
                self.stops += 1
                mode = self._fail_mode()
                if mode == "stop_exit":
                    return ExecResult(1, b"", b"", False)
                if mode == "exec_error":
                    raise RuntimeError("exec failed")
                return None
            if command and command[0] == _PS:
                mode = self._fail_mode()
                if mode == "ps_running":
                    return ExecResult(0, b"learner R 999 bash\n", b"", False)
                if mode == "ps_exit":
                    return ExecResult(1, b"", b"", False)
                return None
            for idx, check_id in enumerate(_CHECK_IDS):
                if command == _CHECK_ARGV[check_id]:
                    self._assert_frozen(f"check {check_id}")
                    if self.outcome == ("exc_check", idx):
                        raise RuntimeError("check exec failed")
                    if self.outcome == ("vanish_check", idx):
                        self.sandbox.vanish(self.handle)
            return None

        self.sandbox.exec_handler = handler

    def _assert_frozen(self, what: str) -> None:
        sb = self.sandbox.sandbox(self.handle)
        running = [p for p in sb.processes if p.user == "learner" and p.state not in ("T", "Z")]
        if running:
            self.violations.append(f"{what} ran while learner processes were running")

    # -- rules ---------------------------------------------------------------

    @precondition(lambda self: not self.ran)
    @rule()
    def grade(self) -> None:
        if self.outcome[0] == "budget":
            self.clock.advance(timedelta(seconds=13))
        self.status = self.job.execute("r1")
        self.ran = True

    @precondition(lambda self: self.ran)
    @rule()
    def redeliver(self) -> None:
        """A duplicate delivery of a finished run is a no-op and must not thaw again."""
        before = len(self.commands)
        assert self.job.execute("r1") is None
        assert len(self.commands) == before

    # -- invariants ----------------------------------------------------------

    @invariant()
    def fail_closed(self) -> None:
        if not self.ran:
            return
        assert not self.violations, self.violations
        cmds = [c for c, _ in self.commands]
        run = self.runs.get("r1")
        assert run is not None
        calls = self.sandbox.calls
        check_idx = [i for i, c in enumerate(cmds) if c in _CHECK_ARGV.values()]
        thaw_idx = [i for i, c in enumerate(cmds) if c == _CONT]
        clean_idx = [
            i for i, c in enumerate(cmds) if c[:2] == ["/bin/bash", "-c"] and "/grader/*" in c[2]
        ]
        gone = self.outcome[0] == "vanish_check"

        # Always: run closed, session handed back, every Check has a recorded result.
        assert run.status is not RunStatus.RUNNING
        assert {r.check_id for r in run.results} == set(_CHECK_IDS)
        session = self.sessions.get("s1")
        assert session is not None and session.status is SessionStatus.READY

        # Thaw: attempted exactly once, after the last Check and the /grader removal.
        if gone:
            assert len(thaw_idx) <= 1  # nothing left to thaw once the Sandbox vanished
        else:
            assert len(thaw_idx) == 1
            assert all(thaw_idx[0] > i for i in check_idx)
            assert clean_idx and thaw_idx[0] > max(clean_idx)

        verified = self._freeze_verified()
        if not verified:
            assert "copy_in" not in calls
            assert not check_idx
            assert self.status is RunStatus.COULD_NOT_COMPLETE
            assert run.status is RunStatus.COULD_NOT_COMPLETE
            assert all(r.outcome is CheckOutcome.COULD_NOT_COMPLETE for r in run.results)
            assert self.mastery.get("u1", "cn1") is None
            if self.outcome[0] == "budget":
                assert not any(c == _STOP for c in cmds)
        else:
            stop_idx = [i for i, c in enumerate(cmds) if c == _STOP]
            first_barrier = [i for i, c in enumerate(cmds) if c == ["<copy_in>"]] + check_idx
            assert not first_barrier or (stop_idx and min(first_barrier) > stop_idx[-1])
            if self.outcome[0] == "success":
                assert self.status is RunStatus.PASSED
            else:
                assert self.status is not RunStatus.PASSED

    def _freeze_verified(self) -> bool:
        if self.outcome[0] == "budget":
            return False
        return bool(self.freeze[0] == "verified")

    def teardown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


FreezeFailClosed.TestCase.settings = settings(
    max_examples=100, stateful_step_count=3, deadline=None
)
TestFreezeFailClosed = FreezeFailClosed.TestCase
