"""Milestone C invariant proof, API side (ai-content task 24.1).

The invariant: learners only ever see content an expert approved.

- A Flagged_Item, flagged through the real ``FlagItem`` use case, is out of every serving
  endpoint for requests received after the flag, and the Review_Task is the only trace.
- A recommendation read through ``GET /v1/path-recommendation`` never contains a concept
  without a servable question: flagging the concept's questions removes it from the response.
- A concept with no servable question answers with the translated RFC 9457 problem document,
  also when the parent is flagged, its pool is empty and no other question exists.

**Validates: Requirements 20.4, 20.5, 22.3**
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from edu_it_api.errors import install_error_handlers
from edu_it_application.ai import FlagItem
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.item_info import ItemInfo
from edu_it_domain.ports.path_recommendations import (
    SOURCE_RULE_BASED,
    AdvisorAuditEntry,
    PathRecommendation,
)
from edu_it_infrastructure.fakes import (
    In_Memory_ItemInfoSource,
    In_Memory_ItemQualityRepository,
    In_Memory_ReviewTaskRepository,
    Sequential_IdGenerator,
)

_AT = datetime(2025, 1, 1, tzinfo=UTC)
_TRACK = "trk-linux"
_HELPERS_PATH = Path(__file__).with_name("test_milestone_b_invariant_api.py")
_REASON = FlagReason(FlagReasonKind.REPORTS)


def _load_helpers() -> ModuleType:
    """Reuse the learning-router world of the Milestone B API proof (imported by path)."""
    name = "_milestone_c_api_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, _HELPERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Flagging:
    """The real ``FlagItem`` over the stores of a world, as the AI worker wires it."""

    def __init__(self, world: Any) -> None:
        info = In_Memory_ItemInfoSource()
        for name in world.questions:
            info.set(ItemKey(ItemKind.QUESTION, name), ItemInfo(_TRACK, "v1"))
        self.tasks = In_Memory_ReviewTaskRepository(world.clock, world.variants)
        self.flag_item = FlagItem(
            flags=world.flags,
            tasks=self.tasks,
            variants=world.variants,
            items=info,
            ids=Sequential_IdGenerator("task"),
        )

    def question(self, name: str) -> None:
        self.flag_item.execute(ItemKey(ItemKind.QUESTION, name), _REASON)

    def variant(self, name: str) -> None:
        self.flag_item.execute(ItemKey(ItemKind.VARIANT, name), _REASON)


def _world(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Flagging]:
    helpers = _load_helpers()
    # The world builds its flag store inside ``__init__``; give it the quality repository.
    monkeypatch.setattr(helpers, "In_Memory_ItemFlagRepository", In_Memory_ItemQualityRepository)
    world: Any = helpers._World()
    world.questions = helpers._QUESTIONS
    install_error_handlers(world.app)
    return world, _Flagging(world)


def _question_ids(world: Any, user: str) -> set[str]:
    helpers = _load_helpers()
    status, body = world.call("GET", "/reviews", user, query=f"concept_id={helpers._CONCEPT}")
    assert status == 200, body
    return {q["stable_id"] for q in body["questions"]}


def test_a_flagged_variant_is_out_of_serving_and_a_flagged_question_leaves_the_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flagging through ``FlagItem`` removes the variant, then the parent, from ``/reviews``.

    **Validates: Requirements 22.3**
    """
    world, flagging = _world(monkeypatch)
    helpers = _load_helpers()
    user = helpers._USERS[0]
    world.add_variant("v-1", VariantState.APPROVED)
    assert world.served_refs() == {"v-1@c1"}

    flagging.variant("v-1")
    assert world.served_refs() == set()
    assert "v-1" not in json.dumps(world.call("GET", "/reviews", user, query="concept_id=con-1"))

    flagging.question("qn-1")
    assert "qn-1" not in _question_ids(world, user)
    assert len(flagging.tasks.page([_TRACK], None, 100).items) == 2  # one open task per flag


def test_a_concept_with_no_servable_question_returns_the_translated_problem_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every question flagged (parent flagged, pool empty, no other question): RFC 9457 404.

    **Validates: Requirements 20.4, 20.5**
    """
    world, flagging = _world(monkeypatch)
    helpers = _load_helpers()
    user = helpers._USERS[0]
    world.add_variant("v-1", VariantState.APPROVED)
    for name in helpers._QUESTIONS:
        flagging.question(name)
    flagging.variant("v-1")  # the pool is empty as well

    status, body = world.call("GET", "/reviews", user, query="concept_id=con-1")

    assert status == 404
    assert body["type"].endswith("no-servable-question")
    assert body["status"] == 404
    assert body["title"]
    assert body["detail"]
    assert body["detail"] != body["title"]
    for secret in ("con-1", "qn-1", "v-1", "Traceback"):
        assert secret not in json.dumps(body)


def test_the_no_servable_question_document_is_translated_in_french_and_english() -> None:
    """The problem document carries a French and an English ``detail``.

    **Validates: Requirements 20.4**
    """
    from edu_it_api.errors.ai import NO_SERVABLE_QUESTION

    translations = NO_SERVABLE_QUESTION.title, NO_SERVABLE_QUESTION.detail
    assert all(translations)
    assert set(NO_SERVABLE_QUESTION.title) >= {"en", "fr"}
    assert set(NO_SERVABLE_QUESTION.detail) >= {"en", "fr"}
    assert NO_SERVABLE_QUESTION.title["en"] != NO_SERVABLE_QUESTION.title["fr"]


# -- recommendations over the real application -----------------------------------------------


def _seed_concept(served: Any) -> ItemKey:
    catalog: Any = served.adapters.catalog
    concept = "concept.advisor-c"
    catalog.add_concept(ConceptRecord(concept))
    question = QuestionRecord(
        "question.advisor-c",
        options=(QuestionOption(option_id=1, is_correct=True),),
        concept_ids=(concept,),
    )
    catalog.add_quiz(QuizRecord("quiz.advisor-c", "lesson.advisor-c", (question,), "v1"))
    return ItemKey(ItemKind.QUESTION, question.stable_id)


def test_a_recommendation_never_contains_a_flagged_item(served: Any) -> None:
    """``GET /v1/path-recommendation`` drops a concept whose only question was flagged.

    **Validates: Requirements 22.3**
    """
    key = _seed_concept(served)
    browser, _ = served.signed_in()
    user = served.user_id(browser)
    served.adapters.path_recommendations.record_run(
        PathRecommendation("rec-c", user, SOURCE_RULE_BASED, ("concept.advisor-c",), "run-c", _AT),
        AdvisorAuditEntry("audit-c", user, "run-c", "h", SOURCE_RULE_BASED, "x", _AT),
    )
    shown = browser.get("/v1/path-recommendation")
    assert shown.status == 200
    assert shown.json()["ordering"] == ["concept.advisor-c"]

    info = In_Memory_ItemInfoSource()
    info.set(key, ItemInfo(_TRACK, "v1"))
    FlagItem(
        flags=served.adapters.item_flags,
        tasks=served.adapters.review_tasks,
        variants=served.adapters.variants,
        items=info,
        ids=Sequential_IdGenerator("task"),
    ).execute(key, _REASON)

    assert browser.get("/v1/path-recommendation").status == 204
