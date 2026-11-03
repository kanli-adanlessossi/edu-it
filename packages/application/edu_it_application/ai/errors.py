"""Errors raised by the reviewer queue use cases (ai-content Req 15.8, 15.10).

Plain exceptions; the API boundary maps the type to a problem document:
``ReviewerExpertiseRequiredError`` to 403 ``reviewer-expertise-required``,
``ReviewTaskNotFoundError`` and ``RecommendationNotFoundError`` to 404,
``InvalidReviewActionError`` to 422. An already decided task surfaces as the domain's
``ReviewTaskAlreadyDecidedError`` (409 ``review-task-already-decided``).
"""

from __future__ import annotations

__all__ = [
    "InvalidReviewActionError",
    "RecommendationNotFoundError",
    "ReviewTaskNotFoundError",
    "ReviewerExpertiseRequiredError",
]


class ReviewerExpertiseRequiredError(Exception):
    """The actor is not a reviewer with expertise in the task's track (403)."""

    def __init__(self, actor_stable_id: str, track_stable_id: str | None = None) -> None:
        super().__init__(f"user {actor_stable_id!r} lacks reviewer expertise")
        self.actor_stable_id = actor_stable_id
        self.track_stable_id = track_stable_id


class ReviewTaskNotFoundError(Exception):
    """The review task does not exist (404)."""

    def __init__(self, stable_id: str) -> None:
        super().__init__(f"review task {stable_id!r} not found")
        self.stable_id = stable_id


class InvalidReviewActionError(Exception):
    """The action does not apply to this task (422).

    ``reason`` is a stable machine code: ``comment_required``, ``body_invalid``,
    ``unsupported_task``, ``unknown_revision`` or ``unchanged_body``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid review action: {reason}")
        self.reason = reason


class RecommendationNotFoundError(Exception):
    """The recommendation does not exist or is not the learner's (404)."""

    def __init__(self, stable_id: str) -> None:
        super().__init__(f"recommendation {stable_id!r} not found")
        self.stable_id = stable_id
