"""Fake_Provider grading-isolation and slow-check tests (task 8.5).

**Validates: Requirements 10.10, 10.12, 11.2, 11.3, 11.4, 11.7**

These tests exercise GradeRun's *call logic* against ``Fake_SandboxProvider``: which calls are
made, as which user, in which order, with which timeouts. They are NOT security evidence
(Requirement 10.12): the fake has no kernel, no real file ownership and no real process
isolation. Real isolation is demonstrated only by the Docker integration tests.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_domain import config
from edu_it_domain.labs.models import CheckOutcome, RunStatus
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox

# Reuse the helpers of test_grade_run.py (tests dir is not a package).
_spec = importlib.util.spec_from_file_location(
    "_grade_run_helpers_iso", Path(__file__).with_name("test_grade_run.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_Env, _lab = _helpers._Env, _helpers._lab

_BUDGET_S = (
    config.LAB_GRADING_RUN_DEADLINE
    - config.LAB_GRADING_FREEZE_BOUND
    - config.LAB_GRADING_TEARDOWN_RESERVE
).total_seconds()


def _spy_copy_in(env: Any) -> list[tuple[Path, str, str | None]]:
    seen: list[tuple[Path, str, str | None]] = []
    original: Callable[..., None] = env.sandbox.copy_in

    def spy(
        handle: SandboxHandle, source_ro: Path, dest: str, *, as_user: str | None = None
    ) -> None:
        seen.append((Path(source_ro), dest, as_user))
        original(handle, source_ro, dest, as_user=as_user)

    env.sandbox.copy_in = spy
    return seen


def test_bundle_copied_from_read_only_source_into_root_owned_grader(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    source = tmp_path / "lab1" / "sandbox"
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    copies = _spy_copy_in(env)
    seen_files: dict[str, bytes] = {}

    def fn(sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command[0] == "/bin/sh":  # snapshot what a Check would see
            seen_files.update(sb.files)
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.PASSED
    # Exactly one copy: only the sandbox/ part (never expected/), as root, into /grader.
    assert copies == [(source, "/grader", "root")]
    assert "/grader/checks/c1.sh" in seen_files
    assert not any("expected" in path for path in seen_files)
    # Source bundle is untouched by grading.
    assert {p: p.read_bytes() for p in source.rglob("*") if p.is_file()} == before
    # Per-Check runs happen as the Grader_User, never as root or the learner.
    check_cmds = [c for c in env.commands if c[0][0] == "/bin/sh"]
    assert check_cmds and all(c[1] == "grader" for c in check_cmds)


def test_grader_directory_removed_as_root_after_grading_before_thaw(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def fn(sb: FakeSandbox, command: list[str], user: str) -> ExecResult | None:
        # Model the real teardown command's effect on the in-memory file map.
        if user == "root" and command[:2] == ["/bin/bash", "-c"] and "rm -rf" in command[2]:
            for path in [p for p in sb.files if p.startswith("/grader/")]:
                del sb.files[path]
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.PASSED
    sb = env.sandbox.sandbox(env.handle)
    assert not [p for p in sb.files if p.startswith("/grader")]
    cmds = env.commands
    clean = next(i for i, c in enumerate(cmds) if c[1] == "root" and "rm -rf" in " ".join(c[0]))
    thaw = next(i for i, c in enumerate(cmds) if c[0] == _helpers._CONT)
    last_check = max(i for i, c in enumerate(cmds) if c[0][0] == "/bin/sh")
    assert last_check < clean < thaw


def test_grader_directory_removed_even_when_a_check_raises(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            raise RuntimeError("boom")
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert any(c[1] == "root" and "rm -rf" in " ".join(c[0]) for c in env.commands)
    assert [c for c in env.commands if c[0] == _helpers._CONT]


def test_slow_check_hits_per_check_timeout_and_later_check_still_runs(tmp_path: Path) -> None:
    env = _Env(tmp_path, _lab(timeout_s=5.0))
    timeouts: dict[str, float | None] = {}
    original: Callable[..., ExecResult] = env.sandbox.exec

    def spy(handle: SandboxHandle, command: Sequence[str], **kw: Any) -> ExecResult:
        if command[0] == "/bin/sh":
            timeouts[command[1]] = kw["timeout_s"]
        return original(handle, command, **kw)

    env.sandbox.exec = spy

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            env.clock.advance(timedelta(seconds=5))  # consumes its whole per-Check timeout
            return ExecResult(-9, b"", b"", True)
        return None

    env.script(fn)
    assert env.job.execute("r1") is RunStatus.FAILED
    assert timeouts["/grader/checks/c1.sh"] == 5.0
    assert timeouts["/grader/checks/c2.sh"] == 5.0  # 12 s budget - 5 s spent = 7 s > 5 s
    assert env.outcomes() == {
        "c1": CheckOutcome.COULD_NOT_COMPLETE,
        "c2": CheckOutcome.PASSED,
    }
    assert env.run().results[0].outcome_code == "timeout"
    assert env.run().results[0].outcome is env.outcomes()["c1"]


def test_slow_check_exhausts_run_deadline_and_remaining_checks_not_executed(
    tmp_path: Path,
) -> None:
    env = _Env(tmp_path, _lab(timeout_s=60.0))
    timeouts: list[float | None] = []
    original: Callable[..., ExecResult] = env.sandbox.exec

    def spy(handle: SandboxHandle, command: Sequence[str], **kw: Any) -> ExecResult:
        if command[0] == "/bin/sh":
            timeouts.append(kw["timeout_s"])
        return original(handle, command, **kw)

    env.sandbox.exec = spy

    def fn(_sb: FakeSandbox, command: list[str], _user: str) -> ExecResult | None:
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            env.clock.advance(timedelta(seconds=_BUDGET_S))  # runs to the capped timeout
            return ExecResult(-9, b"", b"", True)
        return None

    env.script(fn)
    # Timeouts are not an abort: the run is `failed`, identical to a timeout (Req 11.7, 28.x).
    assert env.job.execute("r1") is RunStatus.FAILED
    assert timeouts == [_BUDGET_S]  # capped by the Usable_Run_Budget; c2 never executed
    assert set(env.outcomes().values()) == {CheckOutcome.COULD_NOT_COMPLETE}
    assert env.run().results[1].outcome_code == "no-budget"
    assert env.clock.now() - env.accepted_at <= config.LAB_GRADING_RUN_DEADLINE
    assert [c for c in env.commands if c[0] == _helpers._CONT]  # still thawed
    session = env.sessions.get("s1")
    assert session is not None and session.status.value == "ready"
    assert env.mastery.get("u1", "cn1") is None
