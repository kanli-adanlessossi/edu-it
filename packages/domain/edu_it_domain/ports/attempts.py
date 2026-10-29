"""The attempts repository port (Req 1.2, 3.1, 3.6, 6.1, 10.4, D3 §8).

A learner's assessment attempts — the ``quiz_attempt``, ``answer``, and
``project_submission`` entities of the progress group (D3 §8) — are read and
written through the port declared here. A ``quiz_attempt`` carries the attempt's
score 0-100 and the ``is_best`` flag (Req 3.1, 3.6); an ``answer`` carries
``first_answered_at`` and ``counted_for_mastery`` for the 24-hour anti-farming
window (Req 6.1); a ``project_submission`` carries the ``review_outcome`` from
the ``ProjectReviewer`` extension point, ``None`` while pending (Req 10.4, 10.5).

The domain assessment types are reused unchanged from
:mod:`edu_it_domain.learning.models` —
:class:`~edu_it_domain.learning.models.QuizAttempt`,
:class:`~edu_it_domain.learning.models.Answer`, and
:class:`~edu_it_domain.learning.models.ProjectSubmissionRef` — so this port reads
and writes those domain values and does not redefine them.

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
Every attempt and submission is addressed by its ``stable_id`` (D3 §1.2,
Req 1.1), and a quiz/project/learner by theirs; the internal numeric primary
key of a row is never exposed.

The idempotent dedupe of a duplicate submission by its ``Idempotency-Key``
(failure scenario 4.6) and the content-version pinning of an in-flight attempt
(scenario 4.5) are enforced at the application and infrastructure layers that
build on this contract; the ``answer`` record here carries the
``counted_for_mastery`` flag the anti-farming rule sets (Req 6.1, 6.2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import (
    Answer,
    ProjectSubmissionRef,
    QuizAttempt,
    StableId,
)

__all__ = [
    "AttemptRepository",
]


@runtime_checkable
class AttemptRepository(Protocol):
    """Port for reading and writing quiz attempts, answers, and submissions (Req 3.1, 10.4)."""

    def add_quiz_attempt(
        self,
        user_id: StableId,
        attempt: QuizAttempt,
        answers: tuple[Answer, ...],
    ) -> None:
        """Record ``attempt`` and its ``answers`` as owned by the learner ``user_id``.

        The domain values carry no owner, so the owning learner is passed
        explicitly and attributed to every row written.

        Raises :class:`ValueError` when an attempt with the same ``stable_id``
        already exists. The best-attempt flag across a learner's attempts is
        maintained by the scoring service and persisted on each
        :class:`~edu_it_domain.learning.models.QuizAttempt`
        (:attr:`~edu_it_domain.learning.models.QuizAttempt.is_best`, Req 3.6,
        6.3). Each :class:`~edu_it_domain.learning.models.Answer` carries the
        ``counted_for_mastery`` flag the 24-hour window sets (Req 6.1, 6.2).
        """
        ...

    def get_quiz_attempt(self, stable_id: StableId) -> QuizAttempt | None:
        """Return the quiz attempt with ``stable_id``, or ``None`` when absent."""
        ...

    def list_quiz_attempts(
        self,
        user_id: StableId,
        quiz_id: StableId,
    ) -> tuple[QuizAttempt, ...]:
        """Return the learner's attempts at the quiz, oldest first.

        Backs best-attempt selection — highest score, ties broken by recency
        (Req 3.6, 6.3) — the cooldown computation from the last attempt instant
        (Req 4.1), and the GDPR export of the ``quiz_attempt`` / ``answer``
        tables (Req 22.1).
        """
        ...

    def list_answers_for_question(
        self,
        user_id: StableId,
        question_id: StableId,
    ) -> tuple[Answer, ...]:
        """Return the learner's answers to the question, oldest first.

        The earliest answer's
        :attr:`~edu_it_domain.learning.models.Answer.first_answered_at` anchors
        the 24-hour anti-farming window a re-answer is judged against (Req 6.1,
        6.2, 6.4).
        """
        ...

    def add_project_submission(
        self,
        user_id: StableId,
        submission: ProjectSubmissionRef,
    ) -> None:
        """Record ``submission`` by ``user_id`` for routing through the ``ProjectReviewer``.

        Raises :class:`ValueError` when a submission with the same ``stable_id``
        already exists. The submission is recorded with no
        :attr:`~edu_it_domain.learning.models.ProjectSubmissionRef.review_outcome`
        while the reviewer is pending, and :meth:`save_project_submission` records
        the outcome once the reviewer completes (Req 10.4, 10.5).
        """
        ...

    def get_project_submission(self, stable_id: StableId) -> ProjectSubmissionRef | None:
        """Return the project submission with ``stable_id``, or ``None`` when absent."""
        ...

    def save_project_submission(self, submission: ProjectSubmissionRef) -> None:
        """Update ``submission``, recording the reviewer's outcome once set.

        Keyed by ``stable_id``: writes the
        :attr:`~edu_it_domain.learning.models.ProjectSubmissionRef.review_outcome`
        the ``ProjectReviewer`` produced, which stays ``None`` — and so exposes
        no pass/fail — while the review is pending (Req 10.4, 10.5).
        """
        ...

    def list_project_submissions(
        self,
        user_id: StableId,
    ) -> tuple[ProjectSubmissionRef, ...]:
        """Return every project submission by ``user_id``.

        Backs the GDPR export of the ``project_submission`` table (Req 22.1).
        """
        ...
