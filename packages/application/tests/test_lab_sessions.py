"""Unit tests for StartLab / EndSession / ResetLab / GetSession / ListSessions (task 6.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta

import pytest
from edu_it_application.labs import (
    ActiveSessionConflictError,
    EndSessionUseCase,
    GetSessionUseCase,
    LabNotFoundError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
    ListSessionsUseCase,
    ResetLabUseCase,
    StartLabUseCase,
)
from edu_it_application.learning.errors import IdempotencyConflictError
from edu_it_domain import config
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)


def _lab(lab_id: str) -> LabDefinition:
    return LabDefinition(
        lab_id=lab_id, kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
    )


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.sessions = In_Memory_LabSessionRepository()
        self.queue = In_Memory_TaskQueue()
        catalog = Fake_LabCatalog([_lab("lab-a"), _lab("lab-b")])
        self.start = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=self.queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=Sequential_IdGenerator("s"),
        )
        self.end = EndSessionUseCase(sessions=self.sessions, queue=self.queue, clock=self.clock)
        self.reset = ResetLabUseCase(sessions=self.sessions, end=self.end, start=self.start)

    def jobs(self, name: str) -> list[tuple[str | None, Mapping[str, object]]]:
        return [(j.dedupe_key, j.payload) for j in self.queue._pending if j.job_name == name]


def test_start_creates_provisioning_session_and_enqueues_provision() -> None:
    env = _Env()
    result = env.start.execute("u1", "lab-a", "k1")
    s = result.session
    assert s.status is SessionStatus.PROVISIONING
    assert s.ttl_at == s.created_at + config.LAB_SESSION_TTL
    assert env.jobs("lab.provision") == [(f"provision:{s.stable_id}", {"session_id": s.stable_id})]


def test_idempotent_repeat_replays_and_conflicting_body_is_rejected() -> None:
    env = _Env()
    first = env.start.execute("u1", "lab-a", "k1")
    again = env.start.execute("u1", "lab-a", "k1")
    assert again.replayed and again.session == first.session
    assert len(env.jobs("lab.provision")) == 1
    with pytest.raises(IdempotencyConflictError):
        env.start.execute("u1", "lab-b", "k1")


def test_unknown_lab() -> None:
    with pytest.raises(LabNotFoundError):
        _Env().start.execute("u1", "nope", "k")


@pytest.mark.parametrize(("requested", "same"), [("lab-a", True), ("lab-b", False)])
def test_active_session_conflict_same_or_different_lab(requested: str, same: bool) -> None:
    env = _Env()
    first = env.start.execute("u1", "lab-a", "k1").session
    with pytest.raises(ActiveSessionConflictError) as exc:
        env.start.execute("u1", requested, "k2")
    assert exc.value.same_lab is same
    assert exc.value.active_session_id == first.stable_id
    assert exc.value.active_lab_id == "lab-a"
    assert env.sessions.get_active_for_user("u1") == first
    assert len(env.jobs("lab.provision")) == 1


def test_rate_limit_rejects_with_retry_after_and_creates_nothing() -> None:
    env = _Env()
    for i in range(config.LAB_PROVISION_RATE_LIMIT):
        s = env.start.execute("u1", "lab-a", f"k{i}").session
        env.end.execute("u1", s.stable_id)
        env.clock.advance(timedelta(seconds=1))
    before = len(env.sessions.list_for_user_page("u1", limit=100))
    with pytest.raises(LabProvisionRateLimitedError) as exc:
        env.start.execute("u1", "lab-a", "over")
    assert exc.value.retry_after > timedelta(0)
    assert len(env.sessions.list_for_user_page("u1", limit=100)) == before
    # Another learner is unaffected; the same learner is allowed once the window passes.
    env.start.execute("u2", "lab-a", "x")
    env.clock.advance(config.LAB_PROVISION_RATE_WINDOW)
    env.start.execute("u1", "lab-a", "later")


def test_end_session_cas_and_enqueues_destroy_once() -> None:
    env = _Env()
    s = env.start.execute("u1", "lab-a", "k").session
    ended = env.end.execute("u1", s.stable_id)
    assert ended.status is SessionStatus.ENDED and ended.ended_at == env.clock.now()
    env.end.execute("u1", s.stable_id)  # idempotent
    assert env.jobs("lab.destroy") == [(f"destroy:{s.stable_id}", {"session_id": s.stable_id})]


def test_foreign_and_missing_sessions_are_indistinguishable() -> None:
    env = _Env()
    s = env.start.execute("u1", "lab-a", "k").session
    uses: list[Callable[[str], object]] = [
        lambda sid: env.end.execute("u2", sid),
        lambda sid: GetSessionUseCase(sessions=env.sessions).execute("u2", sid),
    ]
    for use in uses:
        with pytest.raises(LabSessionNotFoundError):
            use(s.stable_id)
        with pytest.raises(LabSessionNotFoundError):
            use("missing")
    assert env.sessions.get(s.stable_id).status is SessionStatus.PROVISIONING  # type: ignore[union-attr]


def test_reset_ends_then_starts_same_lab() -> None:
    env = _Env()
    old = env.start.execute("u1", "lab-b", "k").session
    new = env.reset.execute("u1", old.stable_id, "r1").session
    assert new.stable_id != old.stable_id and new.lab_id == "lab-b"
    assert env.sessions.get(old.stable_id).status is SessionStatus.ENDED  # type: ignore[union-attr]
    assert env.sessions.get_active_for_user("u1") == new
    with pytest.raises(LabSessionNotFoundError):
        env.reset.execute("u2", new.stable_id, "r2")


def test_list_sessions_pages_by_keyset() -> None:
    env = _Env()
    ids = []
    for i in range(3):
        s = env.start.execute("u1", "lab-a", f"k{i}").session
        ids.append(s.stable_id)
        env.end.execute("u1", s.stable_id)
        env.clock.advance(timedelta(seconds=1))
    lister = ListSessionsUseCase(sessions=env.sessions)
    p1 = lister.execute("u1", limit=2)
    assert [s.stable_id for s in p1.items] == ids[:2] and p1.next_after is not None
    p2 = lister.execute("u1", limit=2, after=p1.next_after)
    assert [s.stable_id for s in p2.items] == ids[2:] and p2.next_after is None
    assert lister.execute("u2", limit=2).items == ()
