"""The attempt-quiz use case (Req 18.1, 18.2, 23.1-23.3, 24.1, 24.2, 3, 4, 6, 7.2).

:class:`AttemptQuizUseCase` scores a submitted quiz and persists the result. The
order of work is what makes it safe -- every guard runs before the first side
effect:

1. **Idempotency** (Req 18.2, 23). The ``Idempotency-Key`` is reserved atomically
   at the store first. A repeat with the same body replays the stored result and
   records nothing; a reused key with a different body is rejected; a concurrent
   duplicate is not processed twice.
2. **Cooldown** (Req 24). A submission inside the cooldown is rejected with the
   instant the quiz is attemptable again, and records nothing.
3. **Foreign options** (Req 3.4). A selection naming an option outside its
   question rejects the whole attempt, and records nothing.
4. Only then are the attempt, its answers, mastery changes, and the first review
   schedule persisted.

Mastery rules are all domain calls: percent-correct scoring and best-attempt
selection (scoring), the 24-hour anti-farming window (antifarming), the hint
multiplier (hints), mastery bands and the Mastered gate (mastery), and the single
review entry on first Practiced (review). Numbers come from
:mod:`edu_it_domain.config`; time from the injected ``Clock``.

Known limit: the repository ports expose no unit of work, so the writes of one
submission are sequential rather than transactional. A failure part-way releases
the idempotency key; making the writes atomic is the PostgreSQL adapter's job
(task 20.1).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from edu_it_domain.config import MASTERY_GAIN_PER_CORRECT_ANSWER
from edu_it_domain.learning.antifarming import evaluate_reanswer
from edu_it_domain.learning.cooldown import attemptable_at, is_in_cooldown
from edu_it_domain.learning.hints import tempered_gain
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Answer, Mastery, QuizAttempt, StableId
from edu_it_domain.learning.scoring import is_selection_correct, mark_best, score_attempt
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.idempotency import IdempotencyStore, ReservationStatus
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.review import ReviewRepository

from edu_it_application.learning._support import (
    ensure_review_scheduled,
    has_passed_review,
    load_or_start_mastery,
)
from edu_it_application.learning.errors import (
    IdempotencyConflictError,
    NotFoundError,
    QuizInCooldownError,
    RequestInProgressError,
)

__all__ = [
    "AttemptQuizRequest",
    "AttemptQuizResult",
    "AttemptQuizUseCase",
]

_OPERATION = "quiz-attempt"


@dataclass(frozen=True, slots=True)
class AttemptQuizRequest:
    """A quiz submission."""

    user_id: StableId
    quiz_id: StableId
    selections: Mapping[StableId, int | None]
    """Question ``stable_id`` -> selected ``option_id`` (``None`` = unanswered)."""

    idempotency_key: str
    """The client's ``Idempotency-Key`` for this submission (Req 18.1)."""

    content_version: str | None = None
    """The quiz ``content_version`` the learner started under (Req 25.1).

    ``None`` starts a fresh attempt against the current version (Req 25.2). A
    value naming an older version keeps the in-flight attempt pinned to it.
    """


@dataclass(frozen=True, slots=True)
class AttemptQuizResult:
    """The stored result of one processed submission."""

    attempt: QuizAttempt
    """The recorded attempt."""

    best_attempt: QuizAttempt
    """The learner's best attempt for the quiz after this one (Req 3.6, 6.3)."""

    answers: tuple[Answer, ...]
    """The recorded answers, each with its ``counted_for_mastery`` flag (Req 6)."""

    mastery: tuple[Mastery, ...]
    """Every concept mastery this submission changed."""

    replayed: bool = False
    """``True`` when this is a stored result returned for a repeated key (Req 23.1)."""


def _fingerprint(request: AttemptQuizRequest) -> str:
    """Hash the request body so a reused key with another body is detectable (Req 23.3)."""
    body = json.dumps(
        {
            "user": request.user_id,
            "quiz": request.quiz_id,
            "content_version": request.content_version,
            "selections": sorted(request.selections.items()),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


class AttemptQuizUseCase:
    """Score a quiz submission, enforcing idempotency and cooldown first."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        attempts: AttemptRepository,
        mastery: MasteryRepository,
        reviews: ReviewRepository,
        idempotency: IdempotencyStore,
        clock: Clock,
        ids: IdGenerator,
        lab_check: LabCheck | None = None,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._catalog = catalog
        self._attempts = attempts
        self._mastery = mastery
        self._reviews = reviews
        self._idempotency = idempotency
        self._clock = clock
        self._ids = ids
        self._lab_check = lab_check

    def execute(self, request: AttemptQuizRequest) -> AttemptQuizResult:
        """Process ``request`` at most once per idempotency key.

        Raises:
            IdempotencyConflictError: the key was used with a different body.
            RequestInProgressError: the same request is being processed elsewhere.
            NotFoundError: the quiz, or the pinned ``content_version``, does not exist.
            QuizInCooldownError: the quiz is in cooldown; nothing was recorded.
            ForeignOptionError: a selection is foreign to its question; nothing
                was recorded.
        """
        key = f"{_OPERATION}:{request.user_id}:{request.idempotency_key}"
        reservation = self._idempotency.reserve(key, _fingerprint(request))
        if reservation.status is ReservationStatus.REPLAY:
            stored = reservation.result
            assert isinstance(stored, AttemptQuizResult)
            return replace(stored, replayed=True)
        if reservation.status is ReservationStatus.CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key was reused with a different request body"
            )
        if reservation.status is ReservationStatus.IN_FLIGHT:
            raise RequestInProgressError("an identical request is still being processed")

        try:
            result = self._process(request)
        except Exception:
            self._idempotency.release(key)
            raise
        self._idempotency.complete(key, result)
        return result

    def _process(self, request: AttemptQuizRequest) -> AttemptQuizResult:
        user_id = request.user_id
        now = self._clock.now()

        quiz = self._catalog.get_quiz(request.quiz_id)
        if quiz is None:
            raise NotFoundError("quiz", request.quiz_id)
        # Pin to the version the attempt started under, even if the quiz was edited
        # since; a new attempt (no version) uses the current one (Req 25.1, 25.2).
        # Only the evaluated quiz is read -- existing progress is never rewritten
        # (Req 25.3).
        if request.content_version is not None and request.content_version != quiz.content_version:
            pinned = self._catalog.get_quiz_at_version(quiz.stable_id, request.content_version)
            if pinned is None:
                raise NotFoundError("quiz version", f"{quiz.stable_id}@{request.content_version}")
            quiz = pinned

        prior = self._attempts.list_quiz_attempts(user_id, quiz.stable_id)
        last_attempt_at = max((a.attempted_at for a in prior), default=None)
        if last_attempt_at is not None and is_in_cooldown(last_attempt_at, clock=self._clock):
            raise QuizInCooldownError(quiz.stable_id, attemptable_at(last_attempt_at))

        # Raises ForeignOptionError before anything is written (Req 3.4).
        score = score_attempt(quiz, request.selections)

        hint_reveals = self._mastery.list_hint_reveals(user_id)
        gains: dict[StableId, int] = {}
        answers: list[Answer] = []
        for question in quiz.questions:
            option_id = request.selections.get(question.stable_id)
            if option_id is None:
                continue  # unanswered: incorrect, and no answer row (Req 3.5)

            earlier = self._attempts.list_answers_for_question(user_id, question.stable_id)
            if earlier:
                answer = evaluate_reanswer(
                    question.stable_id, earlier[0].first_answered_at, clock=self._clock
                )
            else:
                answer = Answer(
                    question_id=question.stable_id,
                    first_answered_at=now,
                    counted_for_mastery=True,
                )
            answers.append(answer)

            if not (answer.counted_for_mastery and is_selection_correct(question, option_id)):
                continue
            # Only a hint revealed after the previous submission tempers this one
            # (Req 7.2, 7.4).
            hinted = any(
                reveal.question_id == question.stable_id
                and (last_attempt_at is None or reveal.revealed_at > last_attempt_at)
                for reveal in hint_reveals
            )
            gain = tempered_gain(
                MASTERY_GAIN_PER_CORRECT_ANSWER, hint_revealed_before_submission=hinted
            )
            for concept_id in question.concept_ids:
                gains[concept_id] = gains.get(concept_id, 0) + gain

        attempt = QuizAttempt(
            stable_id=self._ids.new_id(),
            quiz_id=quiz.stable_id,
            score=score,
            is_best=False,
            attempted_at=now,
            content_version=quiz.content_version,
        )
        marked = mark_best((*prior, attempt))
        attempt, best = marked[-1], next(a for a in marked if a.is_best)

        self._attempts.add_quiz_attempt(user_id, attempt, tuple(answers))

        changed: list[Mastery] = []
        for concept_id, delta in gains.items():
            if delta <= 0:
                continue
            schedule = self._reviews.get(user_id, concept_id)
            updated = apply_delta(
                load_or_start_mastery(self._mastery, user_id, concept_id),
                delta,
                clock=self._clock,
                source="quiz",
                review_passed=has_passed_review(schedule),
                lab_check=self._lab_check,
            )
            self._mastery.save(updated)
            ensure_review_scheduled(self._reviews, updated, clock=self._clock)
            changed.append(updated)

        return AttemptQuizResult(
            attempt=attempt,
            best_attempt=best,
            answers=tuple(answers),
            mastery=tuple(changed),
        )
