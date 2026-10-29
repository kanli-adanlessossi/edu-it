"""Rubric evaluation for project submissions (Req 10.1-10.5).

Pure, framework-free service. A submission is scored against its
:class:`~edu_it_domain.learning.models.Rubric`'s weighted criteria: each
criterion contributes its awarded points clamped to ``0..max_points``
(Req 10.1, 10.2). The submission passes iff the weighted score meets or exceeds
:data:`~edu_it_domain.config.RUBRIC_PASS_THRESHOLD` of the rubric's total
``max_points`` (Req 10.2, 10.3). The outcome is produced only once the
``ProjectReviewer`` has completed; while it is pending no outcome exists
(Req 10.4, 10.5). The threshold is read from config, never hardcoded.
"""

from __future__ import annotations

from typing import Final

from edu_it_domain.config import RUBRIC_PASS_THRESHOLD
from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricScore,
)
from edu_it_domain.ports.project_reviewer import ProjectReviewer

#: Recorded ``review_outcome`` for a submission at or above the threshold.
OUTCOME_PASSED: Final[str] = "passed"

#: Recorded ``review_outcome`` for a submission below the threshold.
OUTCOME_NOT_PASSED: Final[str] = "not_passed"


def total_max_points(rubric: Rubric) -> int:
    """Return the sum of every criterion's ``max_points`` (Req 10.2)."""
    return sum(criterion.max_points for criterion in rubric.criteria)


def weighted_score(rubric: Rubric, scores: tuple[RubricScore, ...] | list[RubricScore]) -> int:
    """Return the weighted total of ``scores`` against ``rubric`` (Req 10.1, 10.2).

    Each criterion contributes its awarded points clamped to
    ``0..max_points``; a criterion with no matching score contributes 0.
    """
    awarded = {score.criterion_id: score.points for score in scores}
    return sum(
        min(max(awarded.get(criterion.stable_id, 0), 0), criterion.max_points)
        for criterion in rubric.criteria
    )


def is_passing(rubric: Rubric, scores: tuple[RubricScore, ...] | list[RubricScore]) -> bool:
    """Return whether the weighted score meets the Config pass threshold (Req 10.2, 10.3)."""
    return weighted_score(rubric, scores) >= RUBRIC_PASS_THRESHOLD * total_max_points(rubric)


def evaluate(
    submission: ProjectSubmissionRef,
    rubric: Rubric,
    reviewer: ProjectReviewer,
) -> str | None:
    """Return the submission's ``review_outcome``, or ``None`` while pending.

    Routes the submission through the ``ProjectReviewer``; a reviewer that has
    not completed yields ``None`` and no outcome is recorded (Req 10.4, 10.5).
    Once complete, returns :data:`OUTCOME_PASSED` or :data:`OUTCOME_NOT_PASSED`
    per :func:`is_passing` (Req 10.2, 10.3).
    """
    scores = reviewer.review(submission, rubric)
    if scores is None:
        return None
    return OUTCOME_PASSED if is_passing(rubric, scores) else OUTCOME_NOT_PASSED


__all__ = [
    "OUTCOME_NOT_PASSED",
    "OUTCOME_PASSED",
    "evaluate",
    "is_passing",
    "total_max_points",
    "weighted_score",
]
