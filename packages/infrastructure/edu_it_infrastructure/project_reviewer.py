"""``Pending_ProjectReviewer``: the production project reviewer (Req 11.1, 11.2).

No automated or human grader exists yet, so every submission stays pending:
:meth:`review` returns ``None`` and the domain records no ``review_outcome``.
Production composition wires this adapter and never ``Fake_ProjectReviewer``.
"""

from __future__ import annotations

from edu_it_domain.learning.models import ProjectSubmissionRef, Rubric, RubricScore
from edu_it_domain.ports.project_reviewer import ProjectReviewer

__all__ = ["Pending_ProjectReviewer"]


class Pending_ProjectReviewer(ProjectReviewer):
    """A :class:`ProjectReviewer` that leaves every submission pending."""

    def review(
        self,
        submission: ProjectSubmissionRef,
        rubric: Rubric,
    ) -> tuple[RubricScore, ...] | None:
        """Return ``None`` for every submission (Req 11.1)."""
        return None
