"""Unit tests for PurgeLabData (task 6.15; Req 9.1 to 9.4)."""

from __future__ import annotations

from datetime import timedelta

from edu_it_application.labs import PurgeLabData
from edu_it_domain import config
from edu_it_domain.labs.models import (
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
    TaskCompletion,
)
from edu_it_infrastructure.fakes import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.purge = PurgeLabData(sessions=self.sessions, clock=self.clock)

    def add(self, sid: str, user: str, status: SessionStatus, ended_age: timedelta | None) -> None:
        now = self.clock.now()
        ended = None if ended_age is None else now - ended_age
        s = LabSession(sid, user, "lab", status, now, now + timedelta(hours=1), ended_at=ended)
        self.sessions._sessions[sid] = s  # bypass the one-active rule for seeding
        run = GradingRun(f"r-{sid}", sid, RunStatus.PASSED, now, now + timedelta(seconds=15))
        self.runs.add(run)
        self.runs.add_task_completions(
            run.stable_id, [TaskCompletion(user, f"t-{sid}", run.stable_id, now, True)]
        )


def test_deletes_only_old_ended_sessions_with_runs_and_completions() -> None:
    env = _Env()
    old = config.LAB_RETENTION_PERIOD + timedelta(days=1)
    env.add("old", "u1", SessionStatus.ENDED, old)
    env.add("young", "u2", SessionStatus.ENDED, config.LAB_RETENTION_PERIOD - timedelta(days=1))
    env.add("active", "u3", SessionStatus.READY, None)
    env.add("old-active", "u4", SessionStatus.READY, old)  # never ended: untouched

    assert env.purge.execute() == 1

    assert env.sessions.get("old") is None
    assert env.runs.get("r-old") is None
    assert not env.runs.first_pass_exists("u1", "t-old")
    for sid, user in [("young", "u2"), ("active", "u3"), ("old-active", "u4")]:
        assert env.sessions.get(sid) is not None
        assert env.runs.get(f"r-{sid}") is not None
        assert env.runs.first_pass_exists(user, f"t-{sid}")


def test_boundary_exactly_at_retention_is_kept() -> None:
    env = _Env()
    env.add("edge", "u1", SessionStatus.ENDED, config.LAB_RETENTION_PERIOD)
    assert env.purge.execute() == 0
    env.clock.advance(timedelta(seconds=1))
    assert env.purge.execute() == 1


def test_second_run_at_same_instant_changes_nothing() -> None:
    env = _Env()
    env.add("old", "u1", SessionStatus.ENDED, config.LAB_RETENTION_PERIOD + timedelta(days=1))
    env.add("young", "u2", SessionStatus.ENDED, timedelta(days=1))
    assert env.purge.execute() == 1
    snapshot = (dict(env.sessions._sessions), dict(env.runs._runs), dict(env.runs._completions))
    assert env.purge.execute() == 0
    assert (
        dict(env.sessions._sessions),
        dict(env.runs._runs),
        dict(env.runs._completions),
    ) == snapshot


def test_expired_and_failed_terminal_sessions_are_purged() -> None:
    env = _Env()
    old = config.LAB_RETENTION_PERIOD + timedelta(days=1)
    env.add("exp", "u1", SessionStatus.EXPIRED, old)
    env.add("fail", "u2", SessionStatus.FAILED, old)
    assert env.purge.execute() == 2
