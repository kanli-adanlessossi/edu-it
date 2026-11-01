"""Failure-scenario tests for timer expiry while working or grading (task 8.8).

Drives the real ``ReapSessions``, ``RequestGradingUseCase`` and ``GradeRun`` use cases over
the ``Fake_SandboxProvider``, in-memory repositories and the ``Test_Clock``. Numbers come from
``edu_it_domain.config``.

**Validates: Requirements 7.3, 7.8, 27.1, 27.2, 27.3, 27.4, 27.5, 27.6, 27.7, 27.8**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_application.labs import GradeRun, GradingRejectedError, RecordCheckResult
from edu_it_application.labs.reap_sessions import ReapSessions
from edu_it_application.labs.request_grading import RequestGradingUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckOutcome,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
DEADLINE = config.LAB_GRADING_RUN_DEADLINE
SID = "s1"
USER = "u1"
#: Short enough that the TTL, not the idle timeout, is always the earlier deadline.
TTL = timedelta(minutes=10)
assert TTL < config.LAB_IDLE_TIMEOUT

_LAB = LabDefinition(
    lab_id="lab1",
    kind="terminal",
    image="img",
    concept_ids=("cn1",),
    tasks=(LabTaskDefinition("t1", required=True), LabTaskDefinition("t2", required=True)),
    checks=(LabCheckDefinition("c1", "t1", 5.0), LabCheckDefinition("c2", "t2", 5.0)),
    bundle_path="lab1",
)


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        checks = tmp_path / "lab1" / "sandbox" / "checks"
        checks.mkdir(parents=True)
        for name in ("c1", "c2"):
            (checks / f"{name}.sh").write_text("exit 0\n")
        (tmp_path / "lab1" / "expected").mkdir()
        self.clock = Test_Clock(T0)
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.mastery = In_Memory_MasteryRepository()
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.queue = In_Memory_TaskQueue()
        self.handle = self.sandbox.provision(build_sandbox_spec(_LAB, SID))
        self.sandbox.add_process(self.handle, "learner", "bash")
        self.ttl_at = T0 + TTL
        self.sessions.add_if_no_active(
            LabSession(
                SID,
                USER,
                "lab1",
                SessionStatus.READY,
                T0,
                self.ttl_at,
                sandbox_ref=self.handle.sandbox_id,
            )
        )
        self.reaper = ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name=self.handle.provider,
        )
        self.request = RequestGradingUseCase(
            sessions=self.sessions,
            runs=self.runs,
            queue=self.queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=Sequential_IdGenerator("run"),
        )
        self.grade = GradeRun(
            sessions=self.sessions,
            runs=self.runs,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            record=RecordCheckResult(runs=self.runs, mastery=self.mastery, clock=self.clock),
            clock=self.clock,
            bundle_root=tmp_path,
        )
        self._n = 0

    def at(self, instant: datetime) -> None:
        self.clock.advance(instant - self.clock.now())

    def request_grading(self) -> str:
        self._n += 1
        return self.request.execute(USER, SID, f"key-{self._n}").run.stable_id

    def session(self) -> LabSession:
        got = self.sessions.get(SID)
        assert got is not None
        return got

    def sandbox_alive(self) -> bool:
        return bool(self.sandbox.list_sandboxes())


# -- 27.1 / 27.2: TTL expiry and the strict `now > ttl_at` boundary ------------


def test_ttl_expiry_destroys_sandbox_and_sets_expired(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at + timedelta(seconds=1))
    report = env.reaper.execute()

    assert report.expired == 1
    assert env.session().status is SessionStatus.EXPIRED
    assert not env.session().status.is_active
    assert env.session().ended_at == env.clock.now()
    assert not env.sandbox_alive()


def test_session_at_exactly_ttl_at_is_kept(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at)
    report = env.reaper.execute()

    assert report.expired == 0
    assert env.session().status is SessionStatus.READY
    assert env.sandbox_alive()


def test_session_one_tick_after_ttl_at_is_expired(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at + timedelta(microseconds=1))
    assert env.reaper.execute().expired == 1
    assert env.session().status is SessionStatus.EXPIRED
    assert not env.sandbox_alive()


# -- 27.3 / 27.4: expiry during grading defers; finished run records normally ---


def test_expiry_during_grading_defers_until_run_finishes(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at - timedelta(seconds=1))
    env.request_grading()
    env.at(env.ttl_at + timedelta(seconds=2))  # TTL passed, run still inside its 15 s deadline

    report = env.reaper.execute()
    assert report.expired == 0
    assert report.deferred >= 1
    assert env.session().status is SessionStatus.GRADING
    assert env.sandbox_alive()

    # The job then finishes: the run is recorded normally.
    assert env.grade.execute("run-1") is RunStatus.PASSED
    run = env.runs.get("run-1")
    assert run is not None and run.status is RunStatus.PASSED
    assert {r.outcome for r in run.results} == {CheckOutcome.PASSED}
    assert env.mastery.get(USER, "cn1") is not None
    assert env.session().status is SessionStatus.READY
    assert env.sandbox_alive()  # not destroyed by the finishing job

    # The next tick performs the deferred expiry.
    env.clock.advance(config.LAB_REAPER_INTERVAL)
    assert env.reaper.execute().expired == 1
    assert env.session().status is SessionStatus.EXPIRED
    assert not env.sandbox_alive()


def test_expiry_during_grading_defers_until_the_15s_deadline(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at - timedelta(seconds=1))
    env.request_grading()
    accepted = env.clock.now()

    env.at(accepted + DEADLINE)  # exactly at the deadline: still deferred
    assert env.reaper.execute().expired == 0
    assert env.session().status is SessionStatus.GRADING
    assert env.sandbox_alive()

    env.at(accepted + DEADLINE + timedelta(microseconds=1))  # job never finished
    report = env.reaper.execute()
    assert report.runs_closed == 1
    assert report.expired == 1
    run = env.runs.get("run-1")
    assert run is not None and run.status is RunStatus.COULD_NOT_COMPLETE
    assert env.session().status is SessionStatus.EXPIRED
    assert not env.sandbox_alive()
    assert env.mastery.get(USER, "cn1") is None


# -- 27.4 / 27.6: vanished sandbox -------------------------------------------


def test_vanished_sandbox_records_could_not_complete_without_mastery(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at - timedelta(seconds=1))
    env.request_grading()

    def handler(_sb, command, _user):  # type: ignore[no-untyped-def]
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            env.sandbox.vanish(env.handle)  # TTL reaper destroyed it mid-run
        return None

    env.sandbox.exec_handler = handler
    assert env.grade.execute("run-1") is RunStatus.COULD_NOT_COMPLETE
    run = env.runs.get("run-1")
    assert run is not None
    assert {r.check_id: r.outcome for r in run.results} == {
        "c1": CheckOutcome.COULD_NOT_COMPLETE,
        "c2": CheckOutcome.COULD_NOT_COMPLETE,
    }
    assert env.mastery.get(USER, "cn1") is None
    assert env.runs.list_task_completions(USER) == ()


# -- 27.5 / 27.7 / 27.8: request boundary, finishing after TTL, completions kept


def test_request_at_exactly_ttl_at_is_accepted(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at)
    run_id = env.request_grading()
    run = env.runs.get(run_id)
    assert run is not None and run.status is RunStatus.RUNNING
    assert env.session().status is SessionStatus.GRADING


def test_request_strictly_after_ttl_at_is_rejected(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at + timedelta(microseconds=1))
    with pytest.raises(GradingRejectedError) as err:
        env.request_grading()
    assert err.value.reason is RejectionReason.EXPIRED
    assert env.runs.list_running() == ()
    assert not env.queue.pending
    assert env.session().status is SessionStatus.READY


def test_request_before_ttl_is_processed_even_if_it_finishes_after_ttl(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.at(env.ttl_at - timedelta(seconds=1))
    env.request_grading()

    def handler(_sb, command, _user):  # type: ignore[no-untyped-def]
        if command == ["/bin/sh", "/grader/checks/c1.sh"]:
            env.clock.advance(timedelta(seconds=3))  # crosses ttl_at mid-run
        return None

    env.sandbox.exec_handler = handler
    assert env.grade.execute("run-1") is RunStatus.PASSED
    assert env.clock.now() > env.ttl_at
    run = env.runs.get("run-1")
    assert run is not None and run.status is RunStatus.PASSED
    assert {r.outcome for r in run.results} == {CheckOutcome.PASSED}
    assert {c.task_id for c in env.runs.list_task_completions(USER)} == {"t1", "t2"}


def test_completions_are_kept_after_expiry(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.request_grading()
    assert env.grade.execute("run-1") is RunStatus.PASSED
    before = env.runs.list_task_completions(USER)
    assert {c.task_id for c in before} == {"t1", "t2"}

    env.at(env.ttl_at + timedelta(seconds=1))
    assert env.reaper.execute().expired == 1
    assert env.session().status is SessionStatus.EXPIRED
    assert env.runs.list_task_completions(USER) == before
    assert env.mastery.get(USER, "cn1") is not None
