"""The submit-project use case (Req 18.5, 20.1-20.4, 10).

:class:`SubmitProjectUseCase` validates a submission *before* recording anything:
the rubric self-assessment must be present and cover every rubric criterion with
points in range, and the required evidence must be present (Req 20.1, 20.2). A
rejected submission records no ``project_submission``.

A complete submission is recorded and routed through the ``ProjectReviewer``
extension point (Req 20.3). The outcome is written only once the reviewer
completes; until then the submission is stored with no ``review_outcome`` and is
reported with the distinct :attr:`SubmissionStatus.PENDING` status (Req 10.4,
10.5, 20.4). The pass threshold is the domain rubric service's, from Config.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum

from edu_it_domain.config import MIN_PROJECT_EVIDENCE_COUNT
from edu_it_domain.learning import rubric as rubric_service
from edu_it_domain.learning.models import ProjectSubmissionRef, Rubric, StableId
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.project_reviewer import ProjectReviewer

from edu_it_application.learning.errors import InvalidProjectSubmissionError, NotFoundError

__all__ = [
    "SubmissionStatus",
    "SubmitProjectRequest",
    "SubmitProjectResult",
    "SubmitProjectUseCase",
]


class SubmissionStatus(Enum):
    """Whether a recorded submission has a review outcome yet (Req 20.4)."""

    PENDING = "pending"
    """Recorded, but the reviewer has not completed; no pass/fail is exposed."""

    REVIEWED = "reviewed"
    """The reviewer completed and the outcome is recorded."""


@dataclass(frozen=True, slots=True)
class SubmitProjectRequest:
    """A learner's project submission."""

    user_id: StableId
    project_id: StableId
    self_assessment: Mapping[StableId, int]
    """Rubric criterion ``stable_id`` -> points the learner awards themselves."""

    evidence: Sequence[str]
    """References to the required evidence (links, artifacts)."""


@dataclass(frozen=True, slots=True)
class SubmitProjectResult:
    """The recorded submission and its review status."""

    submission: ProjectSubmissionRef

    @property
    def status(self) -> SubmissionStatus:
        """``PENDING`` until a review outcome exists (Req 20.4)."""
        if self.submission.review_outcome is None:
            return SubmissionStatus.PENDING
        return SubmissionStatus.REVIEWED


def _validate(request: SubmitProjectRequest, rubric: Rubric) -> None:
    """Raise unless the self-assessment and evidence are complete (Req 20.1, 20.2)."""
    if not request.self_assessment:
        raise InvalidProjectSubmissionError(InvalidProjectSubmissionError.MISSING_SELF_ASSESSMENT)

    limits = {c.stable_id: c.max_points for c in rubric.criteria}
    missing = sorted(limits.keys() - request.self_assessment.keys())
    unknown = sorted(request.self_assessment.keys() - limits.keys())
    out_of_range = sorted(
        criterion_id
        for criterion_id, points in request.self_assessment.items()
        if criterion_id in limits and not 0 <= points <= limits[criterion_id]
    )
    if missing or unknown or out_of_range:
        raise InvalidProjectSubmissionError(
            InvalidProjectSubmissionError.INCOMPLETE_SELF_ASSESSMENT,
            f"missing={missing} unknown={unknown} out_of_range={out_of_range}",
        )

    if sum(1 for item in request.evidence if item.strip()) < MIN_PROJECT_EVIDENCE_COUNT:
        raise InvalidProjectSubmissionError(InvalidProjectSubmissionError.MISSING_EVIDENCE)


class SubmitProjectUseCase:
    """Validate, record, and route a project submission through the reviewer."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        attempts: AttemptRepository,
        reviewer: ProjectReviewer,
        ids: IdGenerator,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._catalog = catalog
        self._attempts = attempts
        self._reviewer = reviewer
        self._ids = ids

    def execute(self, request: SubmitProjectRequest) -> SubmitProjectResult:
        """Record the submission if complete, then route it to the reviewer.

        Raises:
            NotFoundError: the project has no rubric.
            InvalidProjectSubmissionError: the self-assessment or evidence is
                incomplete; nothing was recorded.
        """
        rubric = self._catalog.get_rubric_for_project(request.project_id)
        if rubric is None:
            raise NotFoundError("project", request.project_id)
        _validate(request, rubric)

        submission = ProjectSubmissionRef(
            stable_id=self._ids.new_id(),
            project_id=request.project_id,
        )
        self._attempts.add_project_submission(request.user_id, submission)

        outcome = rubric_service.evaluate(submission, rubric, self._reviewer)
        if outcome is not None:
            submission = replace(submission, review_outcome=outcome)
            self._attempts.save_project_submission(submission)
        return SubmitProjectResult(submission=submission)
