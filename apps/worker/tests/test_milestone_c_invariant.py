"""Milestone C invariant proof, worker side (ai-content task 24.1).

The invariant: learners only ever see content an expert approved.

- A Flagged_Item, flagged through the real ``FlagItem`` use case, is out of the review and
  practice sets the worker builds, and a concept left with no servable question raises
  ``NoServableQuestionError`` (the API turns it into the translated problem document).
- A recommendation persisted by ``ai.advise.user`` never contains a concept whose questions are
  all flagged, even when the model proposes it.

**Validates: Requirements 20.4, 20.5, 22.3**
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from edu_it_application.ai import FlagItem, NoServableQuestionError
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.item_info import ItemInfo
from edu_it_infrastructure.fakes import (
    In_Memory_ItemInfoSource,
    In_Memory_ItemQualityRepository,
    Sequential_IdGenerator,
)
from edu_it_worker.ai.advisor_jobs import ADVISE_USER_JOB

T0 = datetime(2025, 1, 1, tzinfo=UTC)
_TESTS = Path(__file__).parent
_REASON = FlagReason(FlagReasonKind.REPORTS)
_USER = "usr_0123456789abcdef"


def _load(filename: str, name: str) -> ModuleType:
    """Reuse the worker wiring of earlier tests (tests are imported by path)."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, _TESTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _flag_item(world: Any, keys: list[ItemKey]) -> FlagItem:
    info = In_Memory_ItemInfoSource()
    for key in keys:
        info.set(key, ItemInfo("track-1", "v1"))
    return FlagItem(
        flags=world.flags,
        tasks=world.tasks,
        variants=world.variants,
        items=info,
        ids=Sequential_IdGenerator("task"),
    )


# -- serving ---------------------------------------------------------------------------------


def _refill_world(monkeypatch: pytest.MonkeyPatch) -> Any:
    helpers = _load("test_ai_worker_refill.py", "_milestone_c_refill_helpers")
    monkeypatch.setattr(helpers, "In_Memory_ItemFlagRepository", In_Memory_ItemQualityRepository)
    return helpers._World(helpers._Parents(), questions=("q1", "q2"))


def test_a_flagged_item_is_out_of_the_worker_review_and_practice_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flagging the variant, then the parent, removes each from every set the worker builds.

    **Validates: Requirements 22.3**
    """
    world = _refill_world(monkeypatch)
    world.add_variant("v-1", VariantState.APPROVED)
    servable = world.worker.servable
    flagging = _flag_item(
        world, [ItemKey(ItemKind.VARIANT, "v-1"), ItemKey(ItemKind.QUESTION, "q1")]
    )

    def review() -> Any:
        return servable.resolve_review_question(
            concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
        )

    assert review().variant_ref == "v-1@v1"

    flagging.execute(ItemKey(ItemKind.VARIANT, "v-1"), _REASON)
    assert review().variant_ref is None
    assert review().question.stable_id == "q1"

    flagging.execute(ItemKey(ItemKind.QUESTION, "q1"), _REASON)
    resolved = review()
    assert resolved.variant_ref is None
    assert resolved.question.stable_id == "q2"
    assert [q.stable_id for q in servable.list_questions_for_concept("con-1")] == ["q2"]


def test_a_concept_with_no_servable_question_raises_for_the_api_problem_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent flagged, pool empty, no other question: nothing is servable (Req 20.5).

    **Validates: Requirements 20.4, 20.5**
    """
    world = _refill_world(monkeypatch)
    world.add_variant("v-1", VariantState.APPROVED)
    flagging = _flag_item(
        world,
        [
            ItemKey(ItemKind.VARIANT, "v-1"),
            ItemKey(ItemKind.QUESTION, "q1"),
            ItemKey(ItemKind.QUESTION, "q2"),
        ],
    )
    for key in (
        ItemKey(ItemKind.VARIANT, "v-1"),
        ItemKey(ItemKind.QUESTION, "q1"),
        ItemKey(ItemKind.QUESTION, "q2"),
    ):
        flagging.execute(key, _REASON)

    servable = world.worker.servable
    with pytest.raises(NoServableQuestionError):
        servable.resolve_review_question(
            concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
        )
    assert servable.list_questions_for_concept("con-1") == ()


# -- recommendations -------------------------------------------------------------------------


def test_a_persisted_recommendation_never_contains_a_flagged_concept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model proposes ``perms`` but its only question is flagged: it is never persisted.

    **Validates: Requirements 22.3**
    """
    helpers = _load("test_ai_worker_advisor.py", "_milestone_c_advisor_helpers")
    monkeypatch.setattr(helpers, "In_Memory_ItemFlagRepository", In_Memory_ItemQualityRepository)

    # Baseline: nothing flagged, the proposal is accepted and holds both concepts.
    baseline: Any = helpers._World()
    baseline.worker.consumers[ADVISE_USER_JOB]({"user_stable_id": _USER})
    first = baseline.recommendations.latest_undismissed(_USER)
    assert first is not None
    assert set(first.ordering) == {"basics", "perms"}

    # Flagged: the same proposal now names a concept with no servable question.
    quality = In_Memory_ItemQualityRepository()
    monkeypatch.setattr(helpers, "In_Memory_ItemFlagRepository", lambda: quality)
    world = helpers._World()
    flagging = FlagItem(
        flags=quality,
        tasks=_tasks(world),
        variants=world.variants,
        items=_info(ItemKey(ItemKind.QUESTION, "q2")),
        ids=Sequential_IdGenerator("task"),
    )
    flagging.execute(ItemKey(ItemKind.QUESTION, "q2"), _REASON)

    world.worker.consumers[ADVISE_USER_JOB]({"user_stable_id": _USER})

    latest = world.recommendations.latest_undismissed(_USER)
    assert latest is not None
    assert "perms" not in latest.ordering
    assert latest.ordering == ("basics",)


def _tasks(world: Any) -> Any:
    from edu_it_infrastructure.fakes import In_Memory_ReviewTaskRepository

    return In_Memory_ReviewTaskRepository(world.clock, world.variants)


def _info(key: ItemKey) -> In_Memory_ItemInfoSource:
    info = In_Memory_ItemInfoSource()
    info.set(key, ItemInfo("track-1", "v1"))
    return info
