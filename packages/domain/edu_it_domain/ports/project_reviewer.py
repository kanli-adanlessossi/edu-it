"""The ``ProjectReviewer`` extension-point port (Req 10.4, 10.5).

A project submission is scored by a configurable reviewer that sits behind this
port — the ``ProjectReviewer`` extension point of the learning design ("New
domain ports"). The reviewer evaluates a
:class:`~edu_it_domain.learning.models.ProjectSubmissionRef`
against the project's :class:`~edu_it_domain.learning.models.Rubric` and returns
the per-criterion :class:`~edu_it_domain.learning.models.RubricScore` the rubric
service then weighs against the Config pass threshold.

Review may be **asynchronous**: a reviewer that has not finished returns
``None``, and the domain records no ``review_outcome`` while the evaluation is
pending, so a pending submission exposes no pass/fail result until the reviewer
completes (Req 10.4, 10.5). This mirrors
:attr:`~edu_it_domain.learning.models.ProjectSubmissionRef.review_outcome`, which
is ``None`` until a completed score exists.

Like the sibling ports this is a framework-free :class:`typing.Protocol`
(``@runtime_checkable``, standard library + domain imports only, no web/API
framework per Req 1.8). The application layer depends on the protocol; the
infrastructure layer supplies a ``Fake_ProjectReviewer`` (configurable score and
a pending outcome, task 1.4) for tests and a production adapter later. As an
extension point, any reviewer implementation — an automated grader, a human
review queue, an AI reviewer — satisfies the port by structural typing without
the domain knowing which is wired in.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricScore,
)

__all__ = [
    "ProjectReviewer",
]


@runtime_checkable
class ProjectReviewer(Protocol):
    """Port for the configurable review of a project submission (Req 10.4, 10.5)."""

    def review(
        self,
        submission: ProjectSubmissionRef,
        rubric: Rubric,
    ) -> tuple[RubricScore, ...] | None:
        """Return the per-criterion scores for ``submission``, or ``None`` while pending.

        Evaluates ``submission`` against ``rubric`` and returns one
        :class:`~edu_it_domain.learning.models.RubricScore` per rubric criterion;
        the rubric service then computes the weighted total against the Config
        pass threshold to decide the outcome (Req 10.1, 10.2, 10.3). While the
        review has not completed the reviewer returns ``None`` and no
        ``review_outcome`` is recorded, so the domain exposes no pass/fail result
        for a pending submission (Req 10.4, 10.5).
        """
        ...
