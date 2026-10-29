"""Application-layer errors raised by the learning use cases.

Each error is a plain exception carrying the structured facts the ``apps/api``
boundary needs to render a translated RFC 9457 problem (task 18.4): the boundary
maps the *type* to a problem type and uses the attributes (for example
:attr:`QuizInCooldownError.attemptable_at`) to build the learner-facing
``detail``. The messages here are diagnostics for logs, never shown to learners.

Like the use cases themselves, this module imports only the standard library and
the domain (Req 1.8).
"""

from __future__ import annotations

from datetime import datetime

from edu_it_domain.learning.models import StableId

__all__ = [
    "IdempotencyConflictError",
    "InsufficientReviewPoolError",
    "InvalidProjectSubmissionError",
    "LearningError",
    "NotEnrolledError",
    "NotFoundError",
    "QuizInCooldownError",
    "RequestInProgressError",
    "ReviewNotAvailableError",
    "ReviewNotDueError",
]


class LearningError(Exception):
    """Base class of every learning use-case failure."""


class NotFoundError(LearningError):
    """A referenced content item or record does not exist (404)."""

    def __init__(self, kind: str, stable_id: StableId) -> None:
        """Record the missing item's ``kind`` (e.g. ``"quiz"``) and ``stable_id``."""
        super().__init__(f"{kind} {stable_id!r} not found")
        self.kind = kind
        self.stable_id = stable_id


class NotEnrolledError(LearningError):
    """The learner is not enrolled in the track (409/404)."""

    def __init__(self, user_id: StableId, track_id: StableId) -> None:
        """Record the learner and track."""
        super().__init__(f"user {user_id!r} is not enrolled in track {track_id!r}")
        self.user_id = user_id
        self.track_id = track_id


class QuizInCooldownError(LearningError):
    """The quiz is in cooldown; no attempt was recorded (Req 24.1, 24.2)."""

    def __init__(self, quiz_id: StableId, attemptable_at: datetime) -> None:
        """Record the quiz and the instant it becomes attemptable again."""
        super().__init__(f"quiz {quiz_id!r} is in cooldown until {attemptable_at.isoformat()}")
        self.quiz_id = quiz_id
        self.attemptable_at = attemptable_at
        """When the quiz can be attempted again; surfaced in the problem ``detail`` (Req 24.2)."""


class IdempotencyConflictError(LearningError):
    """An ``Idempotency-Key`` was reused with a conflicting body (409, Req 23.3)."""


class RequestInProgressError(LearningError):
    """The same keyed request is still being processed elsewhere (Req 23.2).

    Nothing was processed by this call; the client may retry and will receive the
    stored result once the first processing completes.
    """


class InsufficientReviewPoolError(LearningError):
    """The concept has fewer eligible questions than the minimum (Req 28.1, 28.2).

    The review was declined and no schedule entry was created or advanced.
    """

    def __init__(self, concept_id: StableId, available: int, minimum: int) -> None:
        """Record the concept and the pool size against the Config minimum."""
        super().__init__(
            f"concept {concept_id!r} has {available} eligible question(s); minimum is {minimum}"
        )
        self.concept_id = concept_id
        self.available = available
        self.minimum = minimum


class ReviewNotAvailableError(LearningError):
    """The concept has no review schedule yet (it has not reached Practiced)."""

    def __init__(self, concept_id: StableId) -> None:
        """Record the concept."""
        super().__init__(f"concept {concept_id!r} has no review scheduled")
        self.concept_id = concept_id


class ReviewNotDueError(LearningError):
    """The concept's review is not due yet, so it cannot be started or answered."""

    def __init__(self, concept_id: StableId, due_at: datetime) -> None:
        """Record the concept and when its review falls due."""
        super().__init__(f"review of concept {concept_id!r} is due at {due_at.isoformat()}")
        self.concept_id = concept_id
        self.due_at = due_at


class InvalidProjectSubmissionError(LearningError):
    """A project submission is incomplete; nothing was recorded (Req 20.1, 20.2)."""

    MISSING_SELF_ASSESSMENT = "missing_self_assessment"
    INCOMPLETE_SELF_ASSESSMENT = "incomplete_self_assessment"
    MISSING_EVIDENCE = "missing_evidence"

    def __init__(self, reason: str, detail: str = "") -> None:
        """Record the machine-readable ``reason`` and a log-only ``detail``."""
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
