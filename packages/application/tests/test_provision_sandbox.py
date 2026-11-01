"""Unit tests for ProvisionSandbox (task 6.8; Req 6.2, 6.3, 6.6, 7.6, 25.1-25.3)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from edu_it_application.labs import ProvisionOutcome, ProvisionSandbox
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox, ProvisionMode

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)


class _Env:
    def __init__(self, tmp_path: Path, *, with_script: bool = True) -> None:
        self.clock = Test_Clock()
        self.sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.script = tmp_path / "setup.sh"
        self.script.write_text("echo hi\n", encoding="utf-8")
        self.job = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
            setup_scripts=(lambda _lab: self.script) if with_script else None,
        )
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id="s1",
                user_id="u1",
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )

    def session(self) -> LabSession:
        got = self.sessions.get("s1")
        assert got is not None
        return got


def test_success_runs_setup_as_learner_removes_it_and_marks_ready(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    seen: list[tuple[list[str], str]] = []

    def handler(_sb: FakeSandbox, command: Sequence[str], as_user: str) -> ExecResult | None:
        seen.append((list(command), as_user))
        return None

    env.sandbox.exec_handler = handler
    assert env.job.execute("s1") is ProvisionOutcome.READY
    s = env.session()
    assert s.status is SessionStatus.READY and s.sandbox_ref is not None
    assert s.provision_attempts == 1
    assert seen[0] == (
        ["/usr/bin/timeout", "-s", "KILL", "30", "/bin/bash", "/tmp/setup.sh"],
        "learner",
    )
    assert seen[1] == (["/bin/rm", "-f", "/tmp/setup.sh"], "learner")
    assert env.sandbox.calls.count("copy_in") == 1


def test_no_setup_script_still_ready(tmp_path: Path) -> None:
    env = _Env(tmp_path, with_script=False)
    assert env.job.execute("s1") is ProvisionOutcome.READY
    assert "copy_in" not in env.sandbox.calls


def test_duplicate_delivery_exits_without_provisioning(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    assert env.job.execute("s1") is ProvisionOutcome.READY
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED
    assert env.sandbox.calls.count("provision") == 1
    assert env.job.execute("missing") is ProvisionOutcome.SKIPPED


def test_lost_claim_race_exits(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    assert env.sessions.claim_provision_attempt("s1", expected_attempts=0, now=env.clock.now())
    # Stale view: another worker already claimed attempt 1.
    stale = env.sessions.get("s1")
    assert stale is not None
    real_get = env.sessions.get
    env.sessions.get = lambda session_id: stale.__class__(  # type: ignore[method-assign]
        **{**{f: getattr(stale, f) for f in stale.__slots__}, "provision_attempts": 0}
    )
    try:
        assert env.job.execute("s1") is ProvisionOutcome.SKIPPED
    finally:
        env.sessions.get = real_get  # type: ignore[method-assign]
    assert "provision" not in env.sandbox.calls


def test_failure_schedules_retry_ten_seconds_out_then_not_due(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.script_provision(ProvisionMode.FAIL)
    t0 = env.clock.now()
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    s = env.session()
    assert s.status is SessionStatus.PROVISIONING
    assert (
        s.next_provision_at == t0 + config.LAB_PROVISION_MIN_INTERVAL == t0 + timedelta(seconds=10)
    )
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED  # not yet due
    env.clock.advance(timedelta(seconds=10))
    assert env.job.execute("s1") is ProvisionOutcome.READY
    assert env.session().provision_attempts == 2


def test_third_failed_attempt_sets_failed(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.script_provision(ProvisionMode.FAIL, ProvisionMode.FAIL, ProvisionMode.FAIL)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    env.clock.advance(timedelta(seconds=10))
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    env.clock.advance(timedelta(seconds=10))
    assert env.job.execute("s1") is ProvisionOutcome.FAILED
    s = env.session()
    assert s.status is SessionStatus.FAILED and s.provision_attempts == 3
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED
    assert env.sandbox.calls.count("provision") == 3


def test_partial_handle_is_destroyed(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.script_provision(ProvisionMode.PARTIAL)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.sandbox.list_sandboxes() == ()
    assert "destroy" in env.sandbox.calls


def test_provision_timeout_counts_as_failed_attempt(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.script_provision(ProvisionMode.HANG)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.session().sandbox_ref is None


def test_slow_provision_beyond_timeout_destroys_sandbox(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.sandbox.provision_duration = config.LAB_PROVISION_TIMEOUT + timedelta(seconds=1)
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.sandbox.list_sandboxes() == ()


def test_failing_setup_script_destroys_sandbox_and_retries(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def handler(_sb: FakeSandbox, command: Sequence[str], _user: str) -> ExecResult | None:
        if command[-1] == "/tmp/setup.sh":
            return ExecResult(1, b"", b"boom", False)
        return None

    env.sandbox.exec_handler = handler
    assert env.job.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.sandbox.list_sandboxes() == ()
    assert env.session().status is SessionStatus.PROVISIONING


def test_session_ended_meanwhile_destroys_sandbox(tmp_path: Path) -> None:
    env = _Env(tmp_path)

    def handler(_sb: FakeSandbox, _command: Sequence[str], _user: str) -> ExecResult | None:
        env.sessions.transition("s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.ENDED)
        return None

    env.sandbox.exec_handler = handler
    assert env.job.execute("s1") is ProvisionOutcome.SKIPPED
    assert env.session().status is SessionStatus.ENDED
    assert env.sandbox.list_sandboxes() == ()
