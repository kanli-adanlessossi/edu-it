"""The configurable ``ProjectReviewer`` fake (Req 10.4, 10.5).

:class:`Fake_ProjectReviewer` implements the domain
:class:`~edu_it_domain.ports.project_reviewer.ProjectReviewer` extension-point
port entirely in process, with no real grader, human queue, or network call. It
exists so the rubric service and the submit-project use case can be exercised
across both outcomes a reviewer produces:

- a **completed** review returns one
  :class:`~edu_it_domain.learning.models.RubricScore` per rubric criterion, so
  the rubric service can weigh the total against the Config pass threshold
  (Req 10.1-10.3); and
- a **pending** review returns ``None``, so the domain records no
  ``review_outcome`` and a pending submission exposes no pass/fail result until
  the reviewer completes (Req 10.4, 10.5).

Both the per-criterion score and the pending state are configurable, and the
pending state can be toggled at runtime (``complete``) so a test can drive a
submission from pending to reviewed with the same reviewer instance.

The fake is substitutable for a future production adapter without changing
domain code: it satisfies the same ``ProjectReviewer`` protocol by structural
typing (Req 5.2), imports only the domain, and holds no framework dependency.
"""

from __future__ import annotations

from collections.abc import Mapping

from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricScore,
    StableId,
)
from edu_it_domain.ports.project_reviewer import ProjectReviewer

__all__ = ["Fake_ProjectReviewer"]


class Fake_ProjectReviewer(ProjectReviewer):
    """A :class:`ProjectReviewer` with a configurable score and pending state.

    Satisfies the
    :class:`~edu_it_domain.ports.project_reviewer.ProjectReviewer` protocol, so
    the rubric service and the submit-project use case route through it exactly
    as they would a real reviewer (Req 5.2). The score each criterion earns and
    whether the review is still pending are both set by the caller.
    """

    __test__ = False

    def __init__(
        self,
        *,
        scores: Mapping[StableId, int] | None = None,
        default_points: int = 0,
        pending: bool = False,
    ) -> None:
        """Create a reviewer with configurable per-criterion scores and pending state.

        ``scores`` maps a rubric criterion's ``stable_id`` to the points to award
        it; a criterion absent from the mapping earns ``default_points``. When
        ``pending`` is ``True`` the reviewer has not finished, so :meth:`review`
        returns ``None`` and no outcome is recorded until :meth:`complete` is
        called (Req 10.4, 10.5).
        """
        self._scores: dict[StableId, int] = dict(scores) if scores is not None else {}
        self._default_points = default_points
        self._pending = pending

    def review(
        self,
        submission: ProjectSubmissionRef,
        rubric: Rubric,
    ) -> tuple[RubricScore, ...] | None:
        """Return per-criterion scores for ``submission``, or ``None`` while pending.

        While the reviewer is pending this returns ``None`` so the domain records
        no ``review_outcome`` (Req 10.4, 10.5). Once completed it returns one
        :class:`~edu_it_domain.learning.models.RubricScore` per criterion of
        ``rubric`` — the configured points for the criterion, or the configured
        default — in the rubric's criterion order (Req 10.1, 10.2).
        """
        if self._pending:
            return None
        return tuple(
            RubricScore(
                criterion_id=criterion.stable_id,
                points=self._scores.get(criterion.stable_id, self._default_points),
            )
            for criterion in rubric.criteria
        )

    def complete(self) -> None:
        """Mark the review complete so :meth:`review` returns scores (Req 10.5).

        Lets a test drive a submission from pending to reviewed with the same
        reviewer instance; calling it on an already-completed reviewer is a no-op.
        """
        self._pending = False

    def set_pending(self, pending: bool) -> None:
        """Set whether the review is still pending (Req 10.4, 10.5)."""
        self._pending = pending

    @property
    def is_pending(self) -> bool:
        """Whether the reviewer is still pending, for test inspection."""
        return self._pending
