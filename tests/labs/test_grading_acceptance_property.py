"""Property test for grading acceptance.

Feature: hands-on-labs, Property 6: Grading is accepted exactly when the rule says so

**Validates: Requirements 7.3, 7.4, 7.8, 11.1, 11.5, 27.5, 27.7, 30.4, 30.5, 30.6**

Drives the real ``RequestGradingUseCase`` over in-memory repositories and the ``Test_Clock``.
A request is accepted iff the session is ``ready``, ``now <= ttl_at`` and the gap since the last
accepted grading is strictly more than 10 s. An accepted request creates exactly one run
(``accepted_at = now``, ``deadline_at = now + 15 s``) and one ``lab.grade`` job; a rejected one
changes nothing. A request at exactly ``ttl_at`` is accepted, strictly after it is rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.labs import GradingRejectedError, LabSessionNotFoundError
from edu_it_application.labs.request_grading import GRADE_JOB, RequestGradingUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.idempotency import (
    In_Memory_IdempotencyStore,
    Sequential_IdGenerator,
)
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from hypothesis import given, settings
from hypothesis import strategies as st

USER = "user-1"
SESSION = "session-1"
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
MIN_INTERVAL = config.LAB_GRADING_MIN_INTERVAL
RUN_DEADLINE = config.LAB_GRADING_RUN_DEADLINE


class _Env:
    def __init__(
        self,
        *,
        status: SessionStatus,
        ttl_offset: timedelta,
        last_gap: timedelta | None,
    ) -> None:
        """Build a session whose ``ttl_at`` is ``ttl_offset`` after the request time."""
        self.now = T0
        self.clock = Test_Clock(T0)
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.queue = In_Memory_TaskQueue()
        self.session = LabSession(
            stable_id=SESSION,
            user_id=USER,
            lab_id="lab-1",
            status=status,
            created_at=T0 - timedelta(minutes=30),
            ttl_at=T0 + ttl_offset,
            last_grading_accepted_at=None if last_gap is None else T0 - last_gap,
        )
        self.sessions.add_if_no_active(self.session)
        self.use_case = RequestGradingUseCase(
            sessions=self.sessions,
            runs=self.runs,
            queue=self.queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=Sequential_IdGenerator("run"),
        )


def _expected_rejection(
    status: SessionStatus, ttl_offset: timedelta, last_gap: timedelta | None
) -> tuple[RejectionReason, timedelta | None] | None:
    """Independent oracle for the acceptance rule; ``None`` means accepted."""
    if status is SessionStatus.GRADING:
        return RejectionReason.IN_PROGRESS, None
    if status in (SessionStatus.EXPIRED, SessionStatus.IDLE_EXPIRED):
        return RejectionReason.EXPIRED, None
    if status is not SessionStatus.READY:
        return RejectionReason.NOT_READY, None
    if ttl_offset < timedelta(0):  # now is strictly after ttl_at
        return RejectionReason.EXPIRED, None
    if last_gap is not None and last_gap <= MIN_INTERVAL:
        return RejectionReason.THROTTLED, MIN_INTERVAL - last_gap
    return None


_us = st.integers(min_value=-5_000_000, max_value=5_000_000).map(lambda n: timedelta(microseconds=n))
# Offsets weighted to the exact boundary: now == ttl_at, one tick either side, and anything near it.
_ttl_offset = st.one_of(
    st.sampled_from(
        [timedelta(0), timedelta(microseconds=1), timedelta(microseconds=-1), timedelta(seconds=1)]
    ),
    _us,
)
_last_gap = st.one_of(
    st.none(),
    st.sampled_from(
        [
            MIN_INTERVAL,
            MIN_INTERVAL - timedelta(microseconds=1),
            MIN_INTERVAL + timedelta(microseconds=1),
            timedelta(0),
        ]
    ),
    st.integers(min_value=0, max_value=30_000_000).map(lambda n: timedelta(microseconds=n)),
)
_status = st.sampled_from(list(SessionStatus))


# Feature: hands-on-labs, Property 6: Grading is accepted exactly when the rule says so
@settings(max_examples=200, deadline=None)
@given(status=_status, ttl_offset=_ttl_offset, last_gap=_last_gap)
def test_grading_accepted_exactly_when_the_rule_says_so(
    status: SessionStatus, ttl_offset: timedelta, last_gap: timedelta | None
) -> None:
    env = _Env(status=status, ttl_offset=ttl_offset, last_gap=last_gap)
    expected = _expected_rejection(status, ttl_offset, last_gap)

    if expected is None:
        result = env.use_case.execute(USER, SESSION, "key-1")
        run = result.run
        assert not result.replayed
        assert run.status is RunStatus.RUNNING
        assert run.session_id == SESSION
        assert run.accepted_at == T0
        assert run.deadline_at == T0 + RUN_DEADLINE
        assert env.runs.get(run.stable_id) == run
        stored = env.sessions.get(SESSION)
        assert stored is not None
        assert stored.status is SessionStatus.GRADING
        assert stored.last_grading_accepted_at == T0
        assert stored.last_activity_at == T0
        jobs = env.queue.pending_for(GRADE_JOB)
        assert len(jobs) == 1
        assert jobs[0].dedupe_key == f"grade:{run.stable_id}"
        assert jobs[0].payload == {"run_id": run.stable_id, "session_id": SESSION}
        # A repeat of the same Idempotency-Key replays the stored run: no second run or job.
        again = env.use_case.execute(USER, SESSION, "key-1")
        assert again.replayed and again.run == run
        assert len(env.queue.pending_for(GRADE_JOB)) == 1
    else:
        reason, retry_after = expected
        with pytest.raises(GradingRejectedError) as exc:
            env.use_case.execute(USER, SESSION, "key-1")
        assert exc.value.reason is reason
        assert exc.value.retry_after == retry_after
        assert env.sessions.get(SESSION) == env.session  # nothing changed
        assert env.runs.list_running() == ()
        assert env.queue.pending_for(GRADE_JOB) == ()


@pytest.mark.parametrize(
    ("offset", "accepted"),
    [
        (timedelta(0), True),  # exactly ttl_at
        (timedelta(microseconds=-1), False),  # strictly after ttl_at
        (timedelta(seconds=-1), False),
        (timedelta(microseconds=1), True),
    ],
)
def test_request_at_and_after_ttl_at(offset: timedelta, accepted: bool) -> None:
    env = _Env(status=SessionStatus.READY, ttl_offset=offset, last_gap=None)
    if accepted:
        assert env.use_case.execute(USER, SESSION, "k").run.accepted_at == T0
    else:
        with pytest.raises(GradingRejectedError) as exc:
            env.use_case.execute(USER, SESSION, "k")
        assert exc.value.reason is RejectionReason.EXPIRED


@pytest.mark.parametrize(
    ("gap", "accepted"),
    [
        (MIN_INTERVAL, False),  # exactly 10 s is rejected
        (MIN_INTERVAL + timedelta(microseconds=1), True),
    ],
)
def test_ten_second_gap_is_strict(gap: timedelta, accepted: bool) -> None:
    env = _Env(status=SessionStatus.READY, ttl_offset=timedelta(minutes=5), last_gap=gap)
    if accepted:
        env.use_case.execute(USER, SESSION, "k")
    else:
        with pytest.raises(GradingRejectedError) as exc:
            env.use_case.execute(USER, SESSION, "k")
        assert exc.value.reason is RejectionReason.THROTTLED
        assert exc.value.retry_after == timedelta(0)


def test_non_owner_is_indistinguishable_from_missing() -> None:
    env = _Env(status=SessionStatus.READY, ttl_offset=timedelta(minutes=5), last_gap=None)
    for user, session in (("intruder", SESSION), (USER, "missing")):
        with pytest.raises(LabSessionNotFoundError):
            env.use_case.execute(user, session, "k")
    assert env.queue.pending_for(GRADE_JOB) == ()
    assert env.sessions.get(SESSION) == env.session
