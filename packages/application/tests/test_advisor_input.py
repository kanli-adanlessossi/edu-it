"""Unit tests for EligibleItems and the AdvisorInput builder (ai-content task 22.1).

**Validates: Requirements 17.1, 17.3, 17.4**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.ai import AdvisorOrderSchema, EligibleItems, build_advisor_input
from edu_it_domain.ai.inputs import INPUT_FIELDS, InputKind
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    QuestionOption,
    ReviewSchedule,
)
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_infrastructure.fakes import (
    In_Memory_CatalogRepository,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Test_Clock,
)

_NOW = datetime(2025, 1, 1, tzinfo=UTC)
_USER = "usr_0123456789abcdef"


def _question(qid: str, concept: str) -> QuestionRecord:
    options = (QuestionOption(0, True), QuestionOption(1, False))
    return QuestionRecord(qid, options, (concept,))


def _world() -> tuple[In_Memory_CatalogRepository, In_Memory_MasteryRepository]:
    catalog = In_Memory_CatalogRepository()
    catalog.add_concept(ConceptRecord("basics"))
    catalog.add_concept(ConceptRecord("perms", requires=("basics",)))
    catalog.add_concept(ConceptRecord("noquestions"))
    catalog.add_quiz(
        QuizRecord("quiz", "lesson", (_question("q1", "basics"), _question("q2", "perms")))
    )
    return catalog, In_Memory_MasteryRepository()


def _mastery(concept: str, score: int, state: MasteryState) -> Mastery:
    return Mastery(user_id=_USER, concept_id=concept, score=score, state=state)


def test_unlocked_servable_existing_concepts_only() -> None:
    catalog, mastery = _world()
    eligible = EligibleItems(catalog, mastery)
    ids = ["basics", "perms", "noquestions", "ghost"]

    # perms is locked until basics is Practiced; noquestions has no servable
    # question; ghost does not exist.
    assert eligible.for_learner(_USER, ids) == ("basics",)

    mastery.save(_mastery("basics", 40, MasteryState.PRACTICED))
    assert eligible.for_learner(_USER, ids) == ("basics", "perms")


def test_advisor_input_is_pseudonymous_and_allowlisted() -> None:
    catalog, mastery = _world()
    mastery.save(_mastery("basics", 40, MasteryState.PRACTICED))
    reviews = In_Memory_ReviewRepository()
    reviews.save(ReviewSchedule(_USER, "basics", _NOW - timedelta(days=1), timedelta(days=3)))
    clock = Test_Clock(_NOW)
    kwargs = {
        "user_id": _USER,
        "handle_key": "k" * 32,
        "eligible_ids": ["perms", "basics"],
        "catalog": catalog,
        "mastery": mastery,
        "reviews": reviews,
        "clock": clock,
    }

    first = build_advisor_input(run_id="run-1", **kwargs)  # type: ignore[arg-type]
    second = build_advisor_input(run_id="run-2", **kwargs)  # type: ignore[arg-type]

    assert first.handle != second.handle
    assert set(first.llm_input.fields) <= INPUT_FIELDS[InputKind.ADVISOR]
    assert first.llm_input.kind == InputKind.ADVISOR.value
    assert _USER not in json.dumps(dict(first.llm_input.fields))
    assert first.eligible_ids == ("basics", "perms")
    assert first.due == frozenset({"basics"})
    assert first.prerequisites == {"basics": (), "perms": ("basics",)}
    fields = first.llm_input.fields
    assert json.loads(fields["review_state"]) == {"basics": "due", "perms": "none"}
    assert json.loads(fields["concept_mastery"]) == {"basics": "practiced", "perms": "unseen"}
    assert json.loads(fields["completion"]) == {"basics": "in_progress", "perms": "not_started"}


def test_order_schema_accepts_only_an_ordering_list() -> None:
    schema = AdvisorOrderSchema()
    assert schema.validate('{"ordering": ["a", "b"]}') == {"ordering": ["a", "b"]}
    for bad in ("nope", "[]", '{"ordering": "a"}', '{"ordering": [1]}', '{"ordering": [], "x": 1}'):
        with pytest.raises(ValueError, match=r"advisor|ordering"):
            schema.validate(bad)
