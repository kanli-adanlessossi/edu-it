"""Unit tests for ``Pending_ProjectReviewer`` (Req 11.1, 11.2)."""

from __future__ import annotations

from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricCriterion,
)
from edu_it_domain.ports.project_reviewer import ProjectReviewer
from edu_it_infrastructure.project_reviewer import Pending_ProjectReviewer

_RUBRIC = Rubric(
    stable_id="rub-1",
    project_id="proj-1",
    criteria=(RubricCriterion(stable_id="crit-1", max_points=10),),
)


def test_satisfies_project_reviewer_port() -> None:
    assert isinstance(Pending_ProjectReviewer(), ProjectReviewer)


def test_review_returns_none_for_every_submission() -> None:
    reviewer = Pending_ProjectReviewer()
    for sub_id in ("sub-1", "sub-2", "sub-3"):
        submission = ProjectSubmissionRef(stable_id=sub_id, project_id="proj-1")
        assert reviewer.review(submission, _RUBRIC) is None
