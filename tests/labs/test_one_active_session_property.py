"""Property test for the one-active-session invariant.

Feature: hands-on-labs, Property 3: At most one Active_Session per learner

**Validates: Requirements 6.1, 7.1, 7.7, 15.7, 25.6, 30.1, 30.2**

A Hypothesis rule-based state machine drives the real ``StartLab``, ``EndSession`` and
``ResetLab`` use cases over in-memory repositories and the ``Test_Clock``, interleaved with
worker-side ``ready``/``fail``/``expire`` transitions and concurrent starts. After every step no
learner has more than one Active_Session, and the repository agrees with a model of the outcome.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from edu_it_application.labs import (
    ActiveSessionConflictError,
    EndSessionUseCase,
    LabSessionNotFoundError,
    ResetLabUseCase,
    StartLabUseCase,
)
from edu_it_domain.labs.models import ACTIVE_SESSION_STATUSES, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.idempotency import (
    In_Memory_IdempotencyStore,
    Sequential_IdGenerator,
)
from edu_it_infrastructure.fakes.lab_catalog import Fake_LabCatalog
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

USERS = ("user-a", "user-b")
LABS = ("lab-1", "lab-2")
_TERMINAL = frozenset(SessionStatus) - ACTIVE_SESSION_STATUSES


def _lab(lab_id: str) -> LabDefinition:
    return LabDefinition(
        lab_id=lab_id, kind="shell", image="img", concept_ids=(), tasks=(), checks=()
    )


class OneActiveSessionMachine(RuleBasedStateMachine):
    """Model: per learner, the id of the one Active_Session (or none)."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = Test_Clock(datetime(2025, 1, 1, tzinfo=UTC))
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.queue = In_Memory_TaskQueue()
        # The rate limit is covered elsewhere; lift it so it never masks the invariant.
        config = SimpleNamespace(
            LAB_SESSION_TTL=timedelta(minutes=60),
            LAB_PROVISION_RATE_LIMIT=10_000,
            LAB_PROVISION_RATE_WINDOW=timedelta(minutes=10),
        )
        ids = Sequential_IdGenerator("s")
        idem = In_Memory_IdempotencyStore()
        catalog = Fake_LabCatalog(_lab(lab) for lab in LABS)
        self.start_uc = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=self.queue,
            idempotency=idem,
            clock=self.clock,
            ids=ids,
            config=config,
        )
        self.end_uc = EndSessionUseCase(sessions=self.sessions, queue=self.queue, clock=self.clock)
        self.reset_uc = ResetLabUseCase(
            sessions=self.sessions, end=self.end_uc, start=self.start_uc
        )
        self.active: dict[str, str | None] = dict.fromkeys(USERS)
        self._key = 0

    def _next_key(self) -> str:
        self._key += 1
        return f"key-{self._key}"

    def _sync(self, user: str) -> None:
        """Refresh the model from the repository for externally changed sessions."""
        current = self.active[user]
        if current is not None:
            s = self.sessions.get(current)
            if s is None or not s.status.is_active:
                self.active[user] = None

    @rule(user=st.sampled_from(USERS), lab=st.sampled_from(LABS))
    def start(self, user: str, lab: str) -> None:
        self._sync(user)
        had_active = self.active[user]
        try:
            result = self.start_uc.execute(user, lab, self._next_key())
        except ActiveSessionConflictError as err:
            assert had_active is not None
            assert err.active_session_id == had_active
        else:
            assert had_active is None
            assert result.session.status is SessionStatus.PROVISIONING
            self.active[user] = result.session.stable_id

    @rule(user=st.sampled_from(USERS))
    def end(self, user: str) -> None:
        self._sync(user)
        target = self.active[user]
        if target is None:
            return
        ended = self.end_uc.execute(user, target)
        assert ended.status is SessionStatus.ENDED
        self.active[user] = None

    @rule(user=st.sampled_from(USERS), other=st.sampled_from(USERS))
    def end_foreign(self, user: str, other: str) -> None:
        self._sync(user)
        target = self.active[user]
        if target is None or user == other:
            return
        try:
            self.end_uc.execute(other, target)
        except LabSessionNotFoundError:
            pass
        else:  # pragma: no cover - would be a defect
            raise AssertionError("a non-owner ended another learner's session")

    @rule(user=st.sampled_from(USERS))
    def reset(self, user: str) -> None:
        self._sync(user)
        target = self.active[user]
        if target is None:
            return
        result = self.reset_uc.execute(user, target, self._next_key())
        assert result.session.stable_id != target
        assert result.session.lab_id == self.sessions.get(target).lab_id  # type: ignore[union-attr]
        assert self.sessions.get(target).status is SessionStatus.ENDED  # type: ignore[union-attr]
        self.active[user] = result.session.stable_id

    @rule(user=st.sampled_from(USERS))
    def become_ready(self, user: str) -> None:
        self._sync(user)
        target = self.active[user]
        if target is not None:
            self.sessions.transition(
                target, expected={SessionStatus.PROVISIONING}, to=SessionStatus.READY
            )

    @rule(user=st.sampled_from(USERS))
    def fail(self, user: str) -> None:
        self._sync(user)
        target = self.active[user]
        if target is not None:
            assert self.sessions.transition(
                target,
                expected=ACTIVE_SESSION_STATUSES,
                to=SessionStatus.FAILED,
                ended_at=self.clock.now(),
            )
            self.active[user] = None

    @rule(user=st.sampled_from(USERS), idle=st.booleans())
    def expire(self, user: str, idle: bool) -> None:
        self._sync(user)
        target = self.active[user]
        if target is not None:
            assert self.sessions.transition(
                target,
                expected=ACTIVE_SESSION_STATUSES,
                to=SessionStatus.IDLE_EXPIRED if idle else SessionStatus.EXPIRED,
                ended_at=self.clock.now(),
            )
            self.active[user] = None

    @rule(seconds=st.integers(min_value=0, max_value=3600))
    def tick(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))

    @rule(user=st.sampled_from(USERS), n=st.integers(min_value=2, max_value=6))
    def concurrent_starts(self, user: str, n: int) -> None:
        self._sync(user)
        had_active = self.active[user]
        barrier = threading.Barrier(n)
        outcomes: list[object] = [None] * n

        def worker(i: int) -> None:
            barrier.wait()
            try:
                outcomes[i] = self.start_uc.execute(user, LABS[i % len(LABS)], f"c-{self._key}-{i}")
            except Exception as err:  # noqa: BLE001 - classified below
                outcomes[i] = err

        self._key += 1
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [o for o in outcomes if hasattr(o, "session")]
        losers = [o for o in outcomes if isinstance(o, Exception)]
        assert len(winners) + len(losers) == n
        assert all(isinstance(e, ActiveSessionConflictError) for e in losers), losers
        if had_active is None:
            assert len(winners) == 1
            self.active[user] = winners[0].session.stable_id  # type: ignore[attr-defined]
        else:
            assert winners == []

    @invariant()
    def at_most_one_active_per_learner(self) -> None:
        for user in USERS:
            self._sync(user)
            rows = self.sessions.list_for_user_page(user, limit=10_000)
            active = [s for s in rows if s.status in ACTIVE_SESSION_STATUSES]
            assert len(active) <= 1, active
            expected = self.active[user]
            assert (active[0].stable_id if active else None) == expected
            found = self.sessions.get_active_for_user(user)
            assert (found.stable_id if found else None) == expected
        # One provision job per created session at most (dedupe key provision:<id>).
        keys = [j.dedupe_key for j in self.queue.pending_for("lab.provision")]
        assert len(keys) == len(set(keys))


OneActiveSessionMachine.TestCase.settings = settings(
    max_examples=100,
    stateful_step_count=30,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)

# Feature: hands-on-labs, Property 3: At most one Active_Session per learner
TestOneActiveSessionPerLearner = OneActiveSessionMachine.TestCase
