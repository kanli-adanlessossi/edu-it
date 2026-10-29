"""Placement / final-assessment interface and schema checks (Req 21.1, 21.2)."""

from __future__ import annotations

import pytest
from edu_it_api.assessment_schemas import FinalAssessmentResponse, PlacementResponse
from edu_it_domain.ports import assessments
from pydantic import ValidationError


def test_response_schemas_round_trip() -> None:
    placement = PlacementResponse(user_id="u1", track_id="t1", status="pending")
    final = FinalAssessmentResponse(user_id="u1", track_id="t1", status="completed")
    assert placement.model_dump() == {"user_id": "u1", "track_id": "t1", "status": "pending"}
    assert final.status == "completed"


def test_response_schema_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        PlacementResponse(user_id="u1", track_id="t1", status="graded")  # type: ignore[arg-type]


def test_status_values_match_schema_literal() -> None:
    assert {s.value for s in assessments.AssessmentStatus} == {
        "pending",
        "in_progress",
        "completed",
    }


def test_no_grading_implementation_exists() -> None:
    # Grading is a TODO this phase: only Protocols are declared, none implemented.
    assert not hasattr(assessments, "grade_placement")
    assert not hasattr(assessments, "grade_final_assessment")
