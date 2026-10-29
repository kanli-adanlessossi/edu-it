"""Pydantic v2 schemas for placement and final assessment (Req 21.1, 21.2).

Schema level only, backed by the ``placement`` / ``final_assessment`` tables
(D3 §12). Records are addressed by learner and track ``stable_id``; no internal
row key is exposed.

TODO(grading): grading is not implemented in this phase (Req 21.2); there are
no submit/grade request or result schemas and no routes yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AssessmentStatusLiteral",
    "FinalAssessmentResponse",
    "PlacementResponse",
]

AssessmentStatusLiteral = Literal["pending", "in_progress", "completed"]


class _AssessmentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1, max_length=128, description="Learner stable_id.")
    track_id: str = Field(min_length=1, max_length=128, description="Track stable_id.")
    status: AssessmentStatusLiteral


class PlacementResponse(_AssessmentResponse):
    """A learner's placement for a track."""


class FinalAssessmentResponse(_AssessmentResponse):
    """A learner's final assessment for a track."""
