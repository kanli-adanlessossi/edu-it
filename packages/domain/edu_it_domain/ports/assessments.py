"""Placement and final-assessment ports (Req 21.1, 21.2, D3 §12).

Interface level only. The ``placement`` and ``final_assessment`` tables of D3 §12
each hold one assessment per learner x track with a ``status``; neither carries a
``stable_id``, so a record is addressed by the learner's and track's
``stable_id`` (D3 §1.2) and the internal numeric key is never exposed.

TODO(grading): placement and final-assessment grading is intentionally NOT
implemented in this phase (Req 21.2). The grading ports below declare the
signature later phases will implement; no implementation, fake, or caller of
them exists yet. The only behavior tied to placement in this phase is recording
``tested_out`` on ``module_progress`` (Req 21.3), handled separately.

Framework-free ``Protocol`` types, standard library only (Req 1.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import StableId

__all__ = [
    "AssessmentRecord",
    "AssessmentRepository",
    "AssessmentStatus",
    "FinalAssessmentGrader",
    "PlacementGrader",
]


class AssessmentStatus(Enum):
    """Lifecycle ``status`` of a placement or final assessment (D3 §12).

    D3 only fixes the column as a string; these values are the minimal set the
    interface needs and may be extended when grading is designed.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class AssessmentRecord:
    """A ``placement`` or ``final_assessment`` row as the domain sees it."""

    user_id: StableId
    """``stable_id`` of the learner."""

    track_id: StableId
    """``stable_id`` of the track."""

    status: AssessmentStatus
    """Current lifecycle status."""


@runtime_checkable
class AssessmentRepository(Protocol):
    """Persistence of placement and final-assessment records (D3 §12)."""

    def get_placement(self, user_id: StableId, track_id: StableId) -> AssessmentRecord | None:
        """Return the learner's placement for the track, or ``None``."""
        ...

    def save_placement(self, record: AssessmentRecord) -> None:
        """Create or update the learner's placement for the track."""
        ...

    def get_final_assessment(
        self, user_id: StableId, track_id: StableId
    ) -> AssessmentRecord | None:
        """Return the learner's final assessment for the track, or ``None``."""
        ...

    def save_final_assessment(self, record: AssessmentRecord) -> None:
        """Create or update the learner's final assessment for the track."""
        ...


@runtime_checkable
class PlacementGrader(Protocol):
    """TODO: placement grading — not implemented in this phase (Req 21.2)."""

    def grade_placement(self, user_id: StableId, track_id: StableId) -> AssessmentRecord:
        """Grade a placement. TODO: behavior deferred to a later phase."""
        ...


@runtime_checkable
class FinalAssessmentGrader(Protocol):
    """TODO: final-assessment grading — not implemented in this phase (Req 21.2)."""

    def grade_final_assessment(self, user_id: StableId, track_id: StableId) -> AssessmentRecord:
        """Grade a final assessment. TODO: behavior deferred to a later phase."""
        ...
