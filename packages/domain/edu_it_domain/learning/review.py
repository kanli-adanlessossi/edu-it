"""Spaced-review scheduling and question selection (Req 5.4, 9.1-9.6, 28.1-28.3).

Pure, framework-free domain service. It imports no web or API framework
(Req 1.8) and reads time only through the injected ``Clock`` (Req 1.3). Every
business number (initial / passed / failed review intervals and the minimum
eligible-question count) is read from :mod:`edu_it_domain.config`; each is
exposed as an optional keyword defaulting to that config value.

Rules encoded:

- **One entry on first Practiced (Req 5.4, 9.1).** :func:`schedule_on_practiced`
  creates the single entry with ``due_at = clock.now() + initial_interval``.
- **Resume on re-practice (Req 9.2).** If an entry already exists it is returned
  unchanged: no second entry, no reset of interval, due date, or history.
- **Selection (Req 9.3).** :func:`select_review_questions` draws only from the
  eligible pool; an empty pool yields an empty selection.
- **Pass / fail (Req 9.4, 9.5).** :func:`record_review` sets the next interval
  to the passed or failed config interval and appends one attempt.
- **Due / overdue (Req 9.6).** Derived purely from the Clock and ``due_at``.
- **Minimum pool (Req 28.1-28.3).** :func:`start_review` declines (``None``)
  below the minimum, creating or advancing nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from edu_it_domain.config import (
    FAILED_REVIEW_INTERVAL,
    INITIAL_REVIEW_INTERVAL,
    MIN_ELIGIBLE_QUESTION_COUNT,
    PASSED_REVIEW_INTERVAL,
)
from edu_it_domain.learning.models import ReviewAttempt, ReviewSchedule, StableId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from edu_it_domain.ports.catalog import QuestionRecord
    from edu_it_domain.ports.clock import Clock

__all__ = [
    "is_due",
    "is_overdue",
    "is_pool_eligible",
    "overdue_duration",
    "record_review",
    "schedule_on_practiced",
    "select_review_questions",
    "start_review",
]


def schedule_on_practiced(
    existing: ReviewSchedule | None,
    *,
    user_id: StableId,
    concept_id: StableId,
    clock: Clock,
    initial_interval: timedelta = INITIAL_REVIEW_INTERVAL,
) -> ReviewSchedule:
    """Create the single schedule entry, or resume the existing one (Req 9.1, 9.2)."""
    if existing is not None:
        return existing
    return ReviewSchedule(
        user_id=user_id,
        concept_id=concept_id,
        due_at=clock.now() + initial_interval,
        interval=initial_interval,
    )


def record_review(
    schedule: ReviewSchedule,
    *,
    passed: bool,
    clock: Clock,
    passed_interval: timedelta = PASSED_REVIEW_INTERVAL,
    failed_interval: timedelta = FAILED_REVIEW_INTERVAL,
) -> ReviewSchedule:
    """Advance ``schedule`` by one review attempt (Req 9.4, 9.5)."""
    now = clock.now()
    interval = passed_interval if passed else failed_interval
    return replace(
        schedule,
        interval=interval,
        due_at=now + interval,
        history=(*schedule.history, ReviewAttempt(passed=passed, attempted_at=now)),
    )


def is_due(schedule: ReviewSchedule, *, clock: Clock) -> bool:
    """Whether the review is due: the Clock has reached ``due_at`` (Req 9.6)."""
    return clock.now() >= schedule.due_at


def overdue_duration(schedule: ReviewSchedule, *, clock: Clock) -> timedelta:
    """How long past ``due_at`` the Clock is; zero when not yet due (Req 9.6)."""
    return max(clock.now() - schedule.due_at, timedelta(0))


def is_overdue(schedule: ReviewSchedule, *, clock: Clock) -> bool:
    """Whether the Clock is strictly past ``due_at`` (Req 9.6)."""
    return clock.now() > schedule.due_at


def select_review_questions(
    pool: Sequence[QuestionRecord],
    *,
    count: int | None = None,
) -> tuple[QuestionRecord, ...]:
    """Select review questions from the eligible ``pool`` only (Req 9.3).

    Deterministic: takes the first ``count`` distinct questions in pool order
    (the whole pool when ``count`` is ``None``). An empty pool yields ``()``.
    """
    seen: set[StableId] = set()
    unique: list[QuestionRecord] = []
    for question in pool:
        if question.stable_id not in seen:
            seen.add(question.stable_id)
            unique.append(question)
    if count is not None:
        unique = unique[: max(count, 0)]
    return tuple(unique)


def is_pool_eligible(
    pool: Sequence[QuestionRecord],
    *,
    minimum: int = MIN_ELIGIBLE_QUESTION_COUNT,
) -> bool:
    """Whether ``pool`` holds at least ``minimum`` eligible questions (Req 28.3)."""
    return len(pool) >= minimum


def start_review(
    existing: ReviewSchedule | None,
    pool: Sequence[QuestionRecord],
    *,
    user_id: StableId,
    concept_id: StableId,
    clock: Clock,
    minimum: int = MIN_ELIGIBLE_QUESTION_COUNT,
    initial_interval: timedelta = INITIAL_REVIEW_INTERVAL,
) -> ReviewSchedule | None:
    """Start a review, or decline with ``None`` below the minimum pool (Req 28.1, 28.2)."""
    if not is_pool_eligible(pool, minimum=minimum):
        return None
    return schedule_on_practiced(
        existing,
        user_id=user_id,
        concept_id=concept_id,
        clock=clock,
        initial_interval=initial_interval,
    )
