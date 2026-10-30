"""Unit tests for SaveFiles / RequestGrading / RevealLabHint (task 6.3)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_application.labs import (
    GradingRejectedError,
    HintNotAvailableError,
    HintOutOfOrderError,
    LabFilesRejectedError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
    LabTaskNotFoundError,
    RequestGradingUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
)
from edu_it_application.learning.errors import IdempotencyConflictError
from edu_it_domain import config
from edu_it_domain.labs.files import FileViolation
from edu_it_domain.labs.models import LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
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

_LAB = LabDefinition(
    lab_id="lab1",
    kind="code",
    image="img",
    concept_ids=(),
    tasks=(LabTaskDefinition("t1", required=True, hint_count=3),),
    checks=(),
    learner_files=("main.py", "util.py"),
)


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.queue = In_Memory_TaskQueue()
        self.mastery = In_Memory_MasteryRepository()
        self.sandbox = Fake_SandboxProvider(self.clock)
        catalog = Fake_LabCatalog([_LAB])
        self.save = SaveFilesUseCase(
            sessions=self.sessions,
            catalog=catalog,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
        )
        self.grade = RequestGradingUseCase(
            sessions=self.sessions,
            runs=self.runs,
            queue=self.queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=Sequential_IdGenerator("r"),
        )
        self.hint = RevealLabHintUseCase(
            sessions=self.sessions, catalog=catalog, mastery=self.mastery, clock=self.clock
        )

    def ready_session(self, user: str = "u1", sid: str = "s1") -> LabSession:
        now = self.clock.now()
        handle = self.sandbox.provision(_spec(sid))
        session = LabSession(
            stable_id=sid,
            user_id=user,
            lab_id="lab1",
            status=SessionStatus.READY,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            sandbox_ref=handle.sandbox_id,
        )
        self.sessions.add_if_no_active(session)
        return session


def _spec(sid: str):  # type: ignore[no-untyped-def]
    from edu_it_domain.labs.models import SandboxSpec

    return SandboxSpec(sid, "img", "learner", False, 1, 1, 1, 1, True, True, True, (), ())


# --- SaveFiles ---------------------------------------------------------------


def test_save_copies_as_learner_and_records_activity() -> None:
    env = _Env()
    env.ready_session()
    env.clock.advance(timedelta(seconds=30))
    assert env.save.execute("u1", "s1", {"main.py": "print(1)", "util.py": b"x"}) == 2
    sandbox = next(iter(env.sandbox._sandboxes.values()))
    assert sandbox.files["/workspace/main.py"] == b"print(1)"
    assert sandbox.files["/workspace/util.py"] == b"x"
    assert env.sessions.get("s1").last_activity_at == env.clock.now()  # type: ignore[union-attr]


def test_save_rejects_violations_without_copying() -> None:
    env = _Env()
    env.ready_session()
    too_big = {"main.py": "a" * (config.LAB_FILE_MAX_BYTES + 1)}
    for files, violation in [
        (too_big, FileViolation.FILE_TOO_LARGE),
        ({"other.py": "x"}, FileViolation.NAME_NOT_ALLOWED),
        ({"../main.py": "x"}, FileViolation.UNSAFE_NAME),
    ]:
        with pytest.raises(LabFilesRejectedError) as exc:
            env.save.execute("u1", "s1", files)
        assert exc.value.violation is violation
    assert "copy_in" not in env.sandbox.calls
    assert env.sessions.get("s1").last_activity_at is None  # type: ignore[union-attr]


def test_save_non_owner_missing_and_not_ready() -> None:
    env = _Env()
    env.ready_session()
    with pytest.raises(LabSessionNotFoundError):
        env.save.execute("intruder", "s1", {})
    with pytest.raises(LabSessionNotFoundError):
        env.save.execute("u1", "nope", {})
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.GRADING)
    with pytest.raises(LabSessionNotReadyError):
        env.save.execute("u1", "s1", {"main.py": "x"})


# --- RequestGrading ----------------------------------------------------------


def test_grading_accepts_creates_run_with_deadline_and_enqueues() -> None:
    env = _Env()
    env.ready_session()
    env.clock.advance(timedelta(seconds=5))
    result = env.grade.execute("u1", "s1", "k1")
    run = result.run
    assert run.status is RunStatus.RUNNING
    assert run.accepted_at == env.clock.now()
    assert run.deadline_at == run.accepted_at + config.LAB_GRADING_RUN_DEADLINE
    assert env.runs.get(run.stable_id) == run
    session = env.sessions.get("s1")
    assert session is not None
    assert session.status is SessionStatus.GRADING
    assert session.last_grading_accepted_at == run.accepted_at
    assert session.last_activity_at == run.accepted_at
    jobs = [j for j in env.queue._pending if j.job_name == "lab.grade"]
    assert [(j.dedupe_key, j.payload) for j in jobs] == [
        (f"grade:{run.stable_id}", {"run_id": run.stable_id, "session_id": "s1"})
    ]


def test_grading_repeat_key_returns_stored_run_once() -> None:
    env = _Env()
    env.ready_session()
    first = env.grade.execute("u1", "s1", "k1")
    again = env.grade.execute("u1", "s1", "k1")
    assert again.replayed and again.run == first.run
    assert len(env.queue._pending) == 1
    now = env.clock.now()
    env.sessions._sessions["s2"] = LabSession(
        "s2", "u1", "lab1", SessionStatus.ENDED, now, now + config.LAB_SESSION_TTL
    )
    with pytest.raises(IdempotencyConflictError):
        env.grade.execute("u1", "s2", "k1")


def test_grading_non_owner_indistinguishable_from_missing() -> None:
    env = _Env()
    env.ready_session()
    with pytest.raises(LabSessionNotFoundError):
        env.grade.execute("intruder", "s1", "k")
    with pytest.raises(LabSessionNotFoundError):
        env.grade.execute("u1", "nope", "k")
    assert env.sessions.get("s1").status is SessionStatus.READY  # type: ignore[union-attr]


def test_grading_rejections_in_progress_throttled_expired() -> None:
    env = _Env()
    env.ready_session()
    env.grade.execute("u1", "s1", "k1")
    with pytest.raises(GradingRejectedError) as exc:
        env.grade.execute("u1", "s1", "k2")
    assert exc.value.reason is RejectionReason.IN_PROGRESS

    # Worker finished: back to ready, but inside the minimum interval.
    env.sessions.transition("s1", expected={SessionStatus.GRADING}, to=SessionStatus.READY)
    env.clock.advance(config.LAB_GRADING_MIN_INTERVAL)  # exactly the interval: still throttled
    with pytest.raises(GradingRejectedError) as exc:
        env.grade.execute("u1", "s1", "k3")
    assert exc.value.reason is RejectionReason.THROTTLED
    assert exc.value.retry_after == timedelta(0)

    env.clock.advance(timedelta(seconds=1))
    assert env.grade.execute("u1", "s1", "k4").run.status is RunStatus.RUNNING

    env.sessions.transition("s1", expected={SessionStatus.GRADING}, to=SessionStatus.READY)
    env.clock.advance(config.LAB_SESSION_TTL)
    with pytest.raises(GradingRejectedError) as exc:
        env.grade.execute("u1", "s1", "k5")
    assert exc.value.reason is RejectionReason.EXPIRED


def test_rejected_request_releases_its_key() -> None:
    env = _Env()
    env.ready_session()
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.PROVISIONING)
    with pytest.raises(GradingRejectedError) as exc:
        env.grade.execute("u1", "s1", "k1")
    assert exc.value.reason is RejectionReason.NOT_READY
    env.sessions.transition("s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.READY)
    assert not env.grade.execute("u1", "s1", "k1").replayed


# --- RevealLabHint -----------------------------------------------------------


def test_hints_progressive_one_record_per_new_level() -> None:
    env = _Env()
    env.ready_session()
    with pytest.raises(HintOutOfOrderError) as exc:
        env.hint.execute("u1", "s1", "t1", 2)
    assert exc.value.next_level == 1
    assert env.mastery.list_hint_reveals("u1") == ()

    g1 = env.hint.execute("u1", "s1", "t1", 1)
    assert g1.newly_revealed and g1.total_levels == 3
    again = env.hint.execute("u1", "s1", "t1", 1)
    assert not again.newly_revealed
    assert len(env.mastery.list_hint_reveals("u1")) == 1

    with pytest.raises(HintOutOfOrderError):
        env.hint.execute("u1", "s1", "t1", 3)
    env.hint.execute("u1", "s1", "t1", 2)
    env.hint.execute("u1", "s1", "t1", 3)
    reveals = env.mastery.list_hint_reveals("u1")
    assert len(reveals) == 3
    assert all(r.lab_task_id == "t1" and r.question_id is None for r in reveals)


def test_hint_errors() -> None:
    env = _Env()
    env.ready_session()
    with pytest.raises(LabSessionNotFoundError):
        env.hint.execute("intruder", "s1", "t1", 1)
    with pytest.raises(LabTaskNotFoundError):
        env.hint.execute("u1", "s1", "zzz", 1)
    for level in (0, 4):
        with pytest.raises(HintNotAvailableError):
            env.hint.execute("u1", "s1", "t1", level)
    assert env.mastery.list_hint_reveals("u1") == ()
