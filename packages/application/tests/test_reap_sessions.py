"""Unit tests for ReapSessions and DestroySandbox (task 6.13)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from edu_it_application.labs import DestroyOutcome, DestroySandbox, ProvisionSandbox, ReapSessions
from edu_it_domain import config
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import ExecResult, SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.provisioner = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
        )
        self.reaper = ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
        )

    def ready_session(self, sid: str = "s1", user: str = "u1") -> LabSession:
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=sid,
                user_id=user,
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )
        self.provisioner.execute(sid)
        return self.get(sid)

    def get(self, sid: str = "s1") -> LabSession:
        s = self.sessions.get(sid)
        assert s is not None
        return s

    def handle(self, sid: str = "s1") -> SandboxHandle:
        s = self.get(sid)
        assert s.sandbox_ref is not None
        return SandboxHandle(s.sandbox_ref, sid, "fake")

    def alive(self, sid: str = "s1") -> bool:
        return not self.sandbox.sandbox(self.handle(sid)).destroyed

    def start_run(self, sid: str = "s1") -> GradingRun:
        now = self.clock.now()
        self.sessions.transition(sid, expected={SessionStatus.READY}, to=SessionStatus.GRADING)
        run = GradingRun(
            stable_id="r1",
            session_id=sid,
            status=RunStatus.RUNNING,
            accepted_at=now,
            deadline_at=now + timedelta(seconds=15),
        )
        self.runs.add(run)
        return run


def test_keeps_session_at_exactly_ttl_and_expires_just_after() -> None:
    env = _Env()
    # Keep the session non-idle so only the TTL decides.
    s = env.ready_session()
    env.clock.set(s.ttl_at)
    env.sessions.touch_activity("s1", env.clock.now())
    env.reaper.execute()
    assert env.get().status is SessionStatus.READY and env.alive()

    env.clock.advance(timedelta(microseconds=1))
    env.sessions.touch_activity("s1", env.clock.now())
    report = env.reaper.execute()
    assert env.get().status is SessionStatus.EXPIRED
    assert report.expired == 1 and not env.alive()


def test_idle_session_expires_and_sandbox_destroyed() -> None:
    env = _Env()
    env.ready_session()
    env.clock.advance(config.LAB_IDLE_TIMEOUT)
    report = env.reaper.execute()
    assert env.get().status is SessionStatus.IDLE_EXPIRED
    assert report.idle_expired == 1 and not env.alive()


def test_terminal_session_sandbox_and_unknown_orphans_destroyed_then_idempotent() -> None:
    env = _Env()
    env.ready_session("s1", "u1")
    env.ready_session("s2", "u2")
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    orphan = env.sandbox.provision(build_sandbox_spec(_LAB, "ghost"))
    env.reaper.execute()
    assert not env.alive("s1")
    assert env.sandbox.sandbox(orphan).destroyed
    assert env.alive("s2")
    calls = list(env.sandbox.calls)
    second = env.reaper.execute()
    assert second.destroyed == 0 and second.expired == 0
    assert "destroy" not in env.sandbox.calls[len(calls) :]


def test_defers_destruction_while_run_inside_deadline_then_closes_it() -> None:
    env = _Env()
    env.ready_session()
    env.start_run()
    env.sessions.transition("s1", expected={SessionStatus.GRADING}, to=SessionStatus.ENDED)
    env.clock.advance(timedelta(seconds=15))  # exactly at the deadline: still deferred
    report = env.reaper.execute()
    assert env.alive() and report.deferred >= 1
    assert env.runs.get("r1").status is RunStatus.RUNNING  # type: ignore[union-attr]

    env.clock.advance(timedelta(seconds=1))
    env.reaper.execute()
    assert env.runs.get("r1").status is RunStatus.COULD_NOT_COMPLETE  # type: ignore[union-attr]
    assert not env.alive()


def test_active_session_deferred_during_run_even_past_ttl() -> None:
    env = _Env()
    s = env.ready_session()
    env.clock.advance(s.ttl_at - env.clock.now() + timedelta(seconds=1))
    env.start_run()
    env.reaper.execute()
    assert env.get().status is SessionStatus.GRADING and env.alive()


def test_stale_run_closed_and_learner_processes_thawed() -> None:
    env = _Env()
    env.ready_session()
    proc = env.sandbox.add_process(env.handle(), "learner", "bash", state="T")
    env.start_run()
    env.clock.advance(timedelta(seconds=16))
    env.sessions.touch_activity("s1", env.clock.now())
    report = env.reaper.execute()
    assert report.runs_closed == 1
    assert env.runs.get("r1").status is RunStatus.COULD_NOT_COMPLETE  # type: ignore[union-attr]
    assert proc.state == "S"
    assert env.get().status is SessionStatus.READY


def test_failed_thaw_is_retried_next_tick_and_run_stays_running() -> None:
    env = _Env()
    env.ready_session()
    proc = env.sandbox.add_process(env.handle(), "learner", "bash", state="T")
    env.start_run()
    env.clock.advance(timedelta(seconds=16))
    env.sessions.touch_activity("s1", env.clock.now())
    failing = {"on": True}

    def handler(sb: FakeSandbox, command: Sequence[str], as_user: str) -> ExecResult | None:
        if failing["on"] and "CONT" in command:
            return ExecResult(1, b"", b"boom", False)
        return None

    env.sandbox.exec_handler = handler
    first = env.reaper.execute()
    assert first.thaw_failures == 1 and proc.state == "T"
    assert env.runs.get("r1").status is RunStatus.RUNNING  # type: ignore[union-attr]

    failing["on"] = False
    env.reaper.execute()
    assert proc.state == "S"
    assert env.runs.get("r1").status is RunStatus.COULD_NOT_COMPLETE  # type: ignore[union-attr]


def test_destroy_failure_is_logged_and_retried_next_tick() -> None:
    env = _Env()
    env.ready_session()
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    real = env.sandbox.destroy
    env.sandbox.destroy = lambda handle: (_ for _ in ()).throw(RuntimeError("down"))  # type: ignore[method-assign]
    report = env.reaper.execute()
    assert report.destroy_failures == 1 and env.alive()
    env.sandbox.destroy = real  # type: ignore[method-assign]
    env.reaper.execute()
    assert not env.alive()


def test_destroy_sandbox_job_is_idempotent_and_skips_active() -> None:
    env = _Env()
    env.ready_session()
    job = DestroySandbox(
        sessions=env.sessions,
        runs=env.runs,
        sandbox=env.sandbox,
        clock=env.clock,
        provider_name="fake",
    )
    assert job.execute("s1") == DestroyOutcome.NOTHING_TO_DO  # still active
    assert env.alive()
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    assert job.execute("s1") == DestroyOutcome.DESTROYED
    assert not env.alive()
    assert job.execute("s1") == DestroyOutcome.DESTROYED  # repeat is harmless
    assert job.execute("missing") == DestroyOutcome.NOTHING_TO_DO
