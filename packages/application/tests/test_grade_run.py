"""Unit tests for GradeRun and RecordCheckResult (task 6.10)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
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
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox

_STOP = ["/bin/kill", "-s", "STOP", "-1"]
_CONT = ["/bin/kill", "-s", "CONT", "-1"]


def _lab(kind: str = "terminal", timeout_s: float = 5.0) -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind=kind,
        image="img",
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True),),
        checks=(
            LabCheckDefinition("c1", "t1", timeout_s, {"pass": "fb.ok"}),
            LabCheckDefinition("c2", "t1", timeout_s),
        ),
        bundle_path="lab1",
    )


def _ok(stdout: bytes = b"") -> ExecResult:
    return ExecResult(0, stdout, b"", False)


class _Env:
    def __init__(self, tmp_path: Path, lab: LabDefinition | None = None) -> None:
        self.lab = lab or _lab()
        (tmp_path / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (tmp_path / "lab1" / "sandbox" / "checks" / "c1.sh").write_text("exit 0\n")
        (tmp_path / "lab1" / "expected").mkdir()
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
        self.accepted_at = now
        self.runs.add(
            GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
        )
        self.commands: list[tuple[list[str], str, float | None]] = []
        self.job = GradeRun(
            sessions=self.sessions,
            runs=self.runs,
            catalog=Fake_LabCatalog([self.lab]),
            sandbox=self.sandbox,
            record=RecordCheckResult(runs=self.runs, mastery=self.mastery, clock=self.clock),
            clock=self.clock,
            bundle_root=tmp_path,
        )

    def script(self, fn: Callable[[FakeSandbox, list[str], str], ExecResult | None]) -> None:
        def handler(sb: FakeSandbox, command: Sequence[str], as_user: str) -> ExecResult | None:
            self.commands.append((list(command), as_user, None))
            return fn(sb, list(command), as_user)

        self.sandbox.exec_handler = handler

    def run(self) -> GradingRun:
        got = self.runs.get("r1")
        assert got is not None
        return got

    def outcomes(self) -> dict[str, CheckOutcome]:
        return {r.check_id: r.outcome for r in self.run().results}


def test_success_orders_freeze_copy_checks_thaw_and_grants_mastery(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    assert env.job.execute("r1") is RunStatus.PASSED
    assert env.outcomes() == {"c1": CheckOutcome.PASSED, "c2": CheckOutcome.PASSED}
    assert env.run().results[0].feedback_key == "fb.ok"
    assert env.run().status is RunStatus.PASSED
    session = env.sessions.get("s1")
    assert session is not None and session.status is SessionStatus.READY
    mastery = env.mastery.get("u1", "cn1")
    assert mastery is not None and mastery.score > 0
    calls = env.sandbox.calls
    assert calls.index("copy_in") > calls.index("exec")  # freeze exec precedes copy_in
    # learner thawed at the end
    sb = env.sandbox.sandbox(env.handle)
    assert all(p.state != "T" for p in sb.processes if p.user == "learner")


def test_shell_checks_run_as_grader_with_clean_env_and_cleanup(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.script(lambda *_: None)
    env.job.execute("r1")
    cmds = env.commands
    stop = cmds.index((_STOP, "learner", None))
    check = next(i for i, c in enumerate(cmds) if c[0] == ["/bin/sh", "/grader/checks/c1.sh"])
    assert stop < check
    assert cmds[check][1] == "grader"
    assert cmds[check + 1] == (["/bin/kill", "-s", "KILL", "-1"], "grader", None)
    assert cmds[-1] == (_CONT, "learner", None)
    assert sum(1 for c in cmds if c[0] == _CONT) == 1


def test_freeze_not_verified_fails_closed(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == _STOP:
            return ExecResult(1, b"", b"", False)
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert "copy_in" not in env.sandbox.calls
    assert not any(c[0][:1] == ["/bin/sh"] for c in env.commands)
    assert set(env.outcomes().values()) == {CheckOutcome.COULD_NOT_COMPLETE}
    assert len(env.run().results) == 2
    assert env.mastery.get("u1", "cn1") is None
    assert [c for c in env.commands if c[0] == _CONT]  # thawed anyway
    session = env.sessions.get("s1")
    assert session is not None and session.status is SessionStatus.READY


def test_slow_freeze_exhausts_bound(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.call_duration = config.LAB_GRADING_FREEZE_BOUND
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert "copy_in" not in env.sandbox.calls


def test_exhausted_budget_records_all_without_freeze_or_copy(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.script(lambda *_: None)
    env.clock.advance(timedelta(seconds=13))
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert "copy_in" not in env.sandbox.calls
    assert not [c for c in env.commands if c[0] == _STOP]
    assert set(env.outcomes().values()) == {CheckOutcome.COULD_NOT_COMPLETE}
    assert [c for c in env.commands if c[0] == _CONT]


def test_exit_codes_map_to_outcomes_and_mastery_not_granted(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            return ExecResult(1, b"forged PASS", b"", False)
        if command == ["/bin/sh", "/grader/checks/c2.sh"]:
            return ExecResult(90, b"", b"", False)
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.FAILED
    assert env.outcomes() == {
        "c1": CheckOutcome.FAILED,
        "c2": CheckOutcome.COULD_NOT_COMPLETE,
    }
    assert env.run().results[1].outcome_code == "unsafe-path"
    assert env.mastery.get("u1", "cn1") is None


def test_check_timeout_is_capped_by_remaining_budget(tmp_path: Path) -> None:
    env = _Env(tmp_path, _lab(timeout_s=60.0))
    seen: list[float | None] = []
    original = env.sandbox.exec

    def spy(handle: SandboxHandle, command: Sequence[str], **kw: Any) -> ExecResult:
        if command[0] == "/bin/sh":
            seen.append(kw["timeout_s"])
        return original(handle, command, **kw)

    env.sandbox.exec = spy  # type: ignore[method-assign]
    env.job.execute("r1")
    assert seen and seen[0] == 12.0


def test_sandbox_vanishing_marks_rest_could_not_complete(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == ["/bin/sh", "/grader/checks/c2.sh"]:
            env.sandbox.vanish(env.handle)
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert env.outcomes() == {
        "c1": CheckOutcome.PASSED,
        "c2": CheckOutcome.COULD_NOT_COMPLETE,
    }
    session = env.sessions.get("s1")
    assert session is not None and session.status is SessionStatus.READY


def _report(check_id: str, status: str, stdout: bytes = b"ok\n") -> bytes:
    return json.dumps(
        {
            "version": 1,
            "check_id": check_id,
            "status": status,
            "cases": [
                {
                    "case_id": "k1",
                    "exit_status": 0,
                    "timed_out": False,
                    "stdout_b64": base64.b64encode(stdout).decode(),
                    "stderr_b64": "",
                }
            ],
        }
    ).encode()


def _write_expected(tmp_path: Path, check_id: str) -> None:
    doc = {"cases": {"k1": {"stdout_b64": base64.b64encode(b"ok\n").decode(), "exit_status": 0}}}
    (tmp_path / "lab1" / "expected" / f"{check_id}.json").write_text(json.dumps(doc))


def test_code_check_compares_against_expected_read_by_worker(tmp_path: Path) -> None:
    env = _Env(tmp_path, _lab("code"))
    _write_expected(tmp_path, "c1")
    _write_expected(tmp_path, "c2")

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command[:3] == ["/usr/bin/python3", "-I", "/grader/harness/run_cases.py"]:
            out = b"ok\n" if command[3] == "c1" else b"wrong\n"
            return _ok(_report(command[3], "ok", out))
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.FAILED
    assert env.outcomes() == {"c1": CheckOutcome.PASSED, "c2": CheckOutcome.FAILED}


def test_unsafe_learner_file_aborts_run(tmp_path: Path) -> None:
    env = _Env(tmp_path, _lab("code"))
    _write_expected(tmp_path, "c1")
    _write_expected(tmp_path, "c2")

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command[:2] == ["/usr/bin/python3", "-I"]:
            return _ok(_report(command[3], "unsafe-learner-file"))
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert set(env.outcomes().values()) == {CheckOutcome.COULD_NOT_COMPLETE}
    assert len(env.run().results) == 2
    assert env.mastery.get("u1", "cn1") is None
    assert sum(1 for c in env.commands if c[0][:1] == ["/usr/bin/python3"]) == 1
    assert [c for c in env.commands if c[0] == _CONT]


def test_duplicate_delivery_is_skipped(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    assert env.job.execute("r1") is RunStatus.PASSED
    calls = len(env.sandbox.calls)
    assert env.job.execute("r1") is None
    assert len(env.sandbox.calls) == calls
