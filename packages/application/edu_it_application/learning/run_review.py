"""The run-review use cases (Req 18.4, 9, 28).

Two use cases make up the review endpoint:

- :class:`StartReviewUseCase` returns the due review for a concept -- the
  questions drawn from its eligible pool. It declines, creating and advancing
  nothing, when the pool is below the Config minimum (Req 28.1, 28.2).
- :class:`SubmitReviewUseCase` accepts the learner's answers, scores them, and
  records the pass or fail: the schedule's interval lengthens on a pass and
  shortens on a fail (Req 9.4, 9.5), and a pass earns the Config review gain with
  the Mastered gate's ``review_passed`` condition now met (Req 5.6).

Selection is deterministic (:func:`~edu_it_domain.learning.review.select_review_questions`
over a stable-ordered pool), so the questions a learner is shown are exactly the
ones the submission is scored against; no session state is needed. Overdue decay
is applied (idempotently, per instant) before the review result so the gain lands
on the decayed value (Req 8.1, 8.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from edu_it_domain.config import (
    MASTERY_MAX,
    MASTERY_MIN,
    MIN_ELIGIBLE_QUESTION_COUNT,
    REVIEW_PASS_SCORE,
    REVIEW_PASSED_MASTERY_GAIN,
    REVIEW_QUESTION_COUNT,
)
from edu_it_domain.learning.decay import apply_decay
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Mastery, ReviewSchedule, StableId
from edu_it_domain.learning.review import (
    is_due,
    record_review,
    select_review_questions,
    start_review,
)
from edu_it_domain.learning.scoring import ForeignOptionError, is_selection_correct
from edu_it_domain.ports.catalog import CatalogRepository, QuestionRecord
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.review import ReviewRepository

from edu_it_application.learning.errors import (
    InsufficientReviewPoolError,
    NotFoundError,
    ReviewNotAvailableError,
    ReviewNotDueError,
)

__all__ = [
    "StartReviewUseCase",
    "SubmitReviewRequest",
    "SubmitReviewResult",
    "SubmitReviewUseCase",
]


def _review_session(
    catalog: CatalogRepository,
    reviews: ReviewRepository,
    clock: Clock,
    user_id: StableId,
    concept_id: StableId,
) -> tuple[ReviewSchedule, tuple[QuestionRecord, ...]]:
    """Return the due schedule and its session questions, or raise (Req 9.3, 28)."""
    schedule = reviews.get(user_id, concept_id)
    if schedule is None:
        raise ReviewNotAvailableError(concept_id)

    pool = catalog.list_questions_for_concept(concept_id)
    # Decline below the minimum pool; nothing is created or advanced (Req 28.1, 28.2).
    if start_review(schedule, pool, user_id=user_id, concept_id=concept_id, clock=clock) is None:
        raise InsufficientReviewPoolError(concept_id, len(pool), MIN_ELIGIBLE_QUESTION_COUNT)

    if not is_due(schedule, clock=clock):
        raise ReviewNotDueError(concept_id, schedule.due_at)
    return schedule, select_review_questions(pool, count=REVIEW_QUESTION_COUNT)


class StartReviewUseCase:
    """Return the due review for a concept (Req 18.4, 9.3, 28.1)."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        reviews: ReviewRepository,
        clock: Clock,
    ) -> None:
        """Wire the ports the use case reads."""
        self._catalog = catalog
        self._reviews = reviews
        self._clock = clock

    def execute(self, user_id: StableId, concept_id: StableId) -> tuple[QuestionRecord, ...]:
        """Return the questions of the concept's due review.

        Raises:
            ReviewNotAvailableError: the concept has no review scheduled.
            InsufficientReviewPoolError: the pool is below the minimum.
            ReviewNotDueError: the review is not due yet.
        """
        _, questions = _review_session(
            self._catalog, self._reviews, self._clock, user_id, concept_id
        )
        return questions


@dataclass(frozen=True, slots=True)
class SubmitReviewRequest:
    """A learner's answers to a due review."""

    user_id: StableId
    concept_id: StableId
    selections: Mapping[StableId, int | None]
    """Question ``stable_id`` -> selected ``option_id`` (``None`` = unanswered)."""


@dataclass(frozen=True, slots=True)
class SubmitReviewResult:
    """The recorded outcome of a review."""

    passed: bool
    score: int
    schedule: ReviewSchedule
    mastery: Mastery


class SubmitReviewUseCase:
    """Score a review and advance its schedule and the concept's mastery."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        reviews: ReviewRepository,
        mastery: MasteryRepository,
        clock: Clock,
        lab_check: LabCheck | None = None,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._catalog = catalog
        self._reviews = reviews
        self._mastery = mastery
        self._clock = clock
        self._lab_check = lab_check

    def execute(self, request: SubmitReviewRequest) -> SubmitReviewResult:
        """Record the review result.

        Raises:
            ReviewNotAvailableError: the concept has no review scheduled.
            InsufficientReviewPoolError: the pool is below the minimum.
            ReviewNotDueError: the review is not due yet.
            NotFoundError: the concept has no mastery to review.
            ForeignOptionError: a selection is not part of this review, or names
                an option foreign to its question; nothing was recorded.
        """
        user_id, concept_id = request.user_id, request.concept_id
        schedule, questions = _review_session(
            self._catalog, self._reviews, self._clock, user_id, concept_id
        )
        mastery = self._mastery.get(user_id, concept_id)
        if mastery is None:
            raise NotFoundError("mastery", concept_id)

        score = self._score(questions, request.selections)
        passed = score >= REVIEW_PASS_SCORE

        # Decay owed for the overdue period lands first (idempotent per instant).
        mastery = apply_decay(mastery, schedule, clock=self._clock, lab_check=self._lab_check)
        updated_schedule = record_review(schedule, passed=passed, clock=self._clock)
        if passed:
            mastery = apply_delta(
                mastery,
                REVIEW_PASSED_MASTERY_GAIN,
                clock=self._clock,
                source="review",
                review_passed=True,
                lab_check=self._lab_check,
            )

        self._mastery.save(mastery)
        self._reviews.save(updated_schedule)
        return SubmitReviewResult(
            passed=passed,
            score=score,
            schedule=updated_schedule,
            mastery=mastery,
        )

    @staticmethod
    def _score(
        questions: tuple[QuestionRecord, ...],
        selections: Mapping[StableId, int | None],
    ) -> int:
        """Percent correct over the session's questions; unanswered counts wrong (Req 3.1)."""
        by_id = {q.stable_id: q for q in questions}
        for question_id in selections:
            if question_id not in by_id:
                raise ForeignOptionError(f"question {question_id!r} is not part of this review")
        correct = sum(
            1
            for question in questions
            if (option_id := selections.get(question.stable_id)) is not None
            and is_selection_correct(question, option_id)
        )
        percent = round(correct * MASTERY_MAX / len(questions))
        return min(MASTERY_MAX, max(MASTERY_MIN, percent))
