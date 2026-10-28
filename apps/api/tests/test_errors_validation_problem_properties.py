"""Property-based test for validation-failure problem entries (task 8.4).

Feature: foundations, Property 29: Validation failures report one problem entry per rejected field

Property 29 (design.md): *For any* request whose input fails Pydantic v2
validation, the Problem_Details response reports exactly one entry per rejected
field in a problem extension member, each entry giving that field's location and
the reason it was rejected, with no internal representation.

The property is exercised against real Pydantic v2 validation: a model with a
drawn set of required fields is validated with a drawn subset omitted and/or
given the wrong type, so the ``ValidationError`` carries one error per rejected
field (every field here raises at most one error). ``build_validation_problem``
must then produce exactly one ``fields`` entry per rejected field, each carrying
the field's dotted location and a non-empty reason, and the serialized body must
never echo the rejected input value or Pydantic's internal ``input`` key.

Specific examples (the sign-up model, the handler wiring) are covered by the
example unit tests in ``test_errors_problem_details.py``.

Validates: Requirements 7.8, 19.5

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from edu_it_api.errors import build_validation_problem
from edu_it_api.errors.catalog import VALIDATION
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError, create_model

# Field names are drawn from a small alphabet so distinct names are likely to
# repeat across examples while staying valid Python identifiers for the model.
# A leading underscore is excluded because Pydantic treats such names as private
# attributes rather than model fields, so they would not be validated at all.
_field_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12).filter(
    lambda name: name.isidentifier() and not name.startswith("_")
)

# A rejection for a given field is either "omit it" (triggers "Field required")
# or "send the wrong type" (an int where a str is required -> a type error). A
# sentinel distinguishes the two so the input dict can be built accordingly.
_OMIT = object()
_rejections = st.sampled_from([_OMIT, 12345])


@st.composite
def _rejected_models(draw: st.DrawFn) -> tuple[type[BaseModel], dict[str, object], int]:
    """Draw a model plus an input that rejects every modeled field exactly once.

    Returns the dynamically built Pydantic model, the invalid input payload, and
    the expected number of rejected fields. Each field is a required ``str``; the
    input either omits it (one "required" error) or supplies an ``int`` (one type
    error), so the number of validation errors equals the number of fields and
    each rejected field maps to exactly one error.
    """
    names = draw(st.lists(_field_names, min_size=1, max_size=6, unique=True))
    field_defs = {name: (str, ...) for name in names}
    model = create_model("DrawnModel", **field_defs)  # type: ignore[call-overload]

    payload: dict[str, object] = {}
    for name in names:
        rejection = draw(_rejections)
        if rejection is not _OMIT:
            payload[name] = rejection
    return model, payload, len(names)


@given(case=_rejected_models())
@settings(max_examples=200)
def test_property_29_one_problem_entry_per_rejected_field(
    case: tuple[type[BaseModel], dict[str, object], int],
) -> None:
    """Feature: foundations, Property 29: Validation failures report one problem entry per rejected field.

    Validating a drawn payload that rejects every modeled field produces a
    ``ValidationError`` with one error per field; ``build_validation_problem``
    reports exactly one entry per rejected field, each with a dotted location and
    a non-empty reason, and never leaks the rejected input value (Req 7.8, 19.5).
    """
    model, payload, expected_count = case

    try:
        model.model_validate(payload)
    except ValidationError as exc:
        errors: list[dict[str, Any]] = [dict(e) for e in exc.errors()]
    else:  # pragma: no cover - the drawn payload always rejects every field.
        raise AssertionError("expected a ValidationError for the drawn payload")

    # Guard the generator's invariant: one Pydantic error per modeled field.
    assert len(errors) == expected_count

    problem = build_validation_problem(
        status=int(HTTPStatus.UNPROCESSABLE_ENTITY),
        locale="en",
        errors=errors,
        problem_type=VALIDATION,
    )

    # One extension entry per rejected field (Req 19.5).
    assert problem.fields is not None
    assert len(problem.fields) == len(errors)

    # Each entry maps to a distinct error location and carries a non-empty reason.
    expected_locations = [".".join(str(part) for part in e["loc"]) for e in errors]
    entry_locations = [entry.location for entry in problem.fields]
    assert sorted(entry_locations) == sorted(expected_locations)
    assert all(entry.reason for entry in problem.fields)

    # No internal representation leaks: Pydantic's ``input`` echo and the rejected
    # value (12345) must never appear in the serialized body (Req 19.5 "no
    # internal representation").
    serialized = json.dumps(problem.to_response_body())
    assert '"input"' not in serialized
    assert "12345" not in serialized
