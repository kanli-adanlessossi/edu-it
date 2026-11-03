"""Tests for ``serving.py`` and ``ServableCatalog`` (ai-content task 14.1).

**Validates: Requirements 14.1, 14.2, 14.3, 20.1, 20.3, 20.4, 20.5**
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import edu_it_application.ai.servable_catalog as servable_module
import edu_it_domain.ai.serving as serving_module
import pytest
from edu_it_application.ai import NoServableQuestionError, ResolvedQuestion, ServableCatalog
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import In_Memory_VariantRepository, Test_Clock
from edu_it_infrastructure.fakes.item_flags import In_Memory_ItemFlagRepository

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
CONCEPT = "con-1"
S = VariantState


def _q(stable_id: str) -> QuestionRecord:
    return QuestionRecord(
        stable_id,
        (QuestionOption(1, True), QuestionOption(2, False)),
        (CONCEPT,),
    )


class _Catalog:
    """A small real CatalogRepository over fixed questions."""

    def __init__(self, questions: list[QuestionRecord]) -> None:
        self.questions = questions

    def get_module(self, stable_id: str) -> None:
        return None

    def list_modules_for_track(self, track_id: str) -> tuple[()]:
        return ()

    def get_concept(self, stable_id: str) -> None:
        return None

    def get_quiz(self, stable_id: str) -> QuizRecord:
        return QuizRecord(stable_id, "les", tuple(self.questions))

    def get_quiz_at_version(self, stable_id: str, content_version: str) -> QuizRecord:
        return self.get_quiz(stable_id)

    def list_questions_for_concept(self, concept_id: str) -> tuple[QuestionRecord, ...]:
        return tuple(sorted(self.questions, key=lambda q: q.stable_id))

    def get_rubric_for_project(self, project_id: str) -> None:
        return None


class _World:
    def __init__(self, *questions: str) -> None:
        self.variants = In_Memory_VariantRepository(Test_Clock(_T0))
        self.flags = In_Memory_ItemFlagRepository()
        self.catalog = ServableCatalog(
            _Catalog([_q(name) for name in questions]),
            self.flags,
            self.variants,
        )

    def variant(
        self,
        name: str,
        parent: str = "q1",
        state: VariantState = S.APPROVED,
        version: str = "c1",
    ) -> None:
        self.variants.add(NewVariant(name, parent, "trk", state))
        body = json.dumps(
            {"options": [{"is_correct": False}, {"is_correct": True}, {"is_correct": False}]}
        )
        self.variants.add_revision(NewRevision(name, version, body, state=RevisionState.APPROVED))
        stored = self.variants.get(name)
        assert stored is not None
        self.variants.save(replace(stored, approved_content_version=version))

    def flag(self, kind: ItemKind, stable_id: str) -> None:
        self.flags.flag(ItemKey(kind, stable_id), FlagReason(FlagReasonKind.REPORTS))

    def resolve(
        self, variant_ref: str | None = None, user: str = "u", due: datetime = _T0
    ) -> ResolvedQuestion:
        return self.catalog.resolve_review_question(
            concept_id=CONCEPT,
            parent_question_id="q1",
            user_ref=user,
            due_at=due,
            variant_ref=variant_ref,
        )


def test_serves_approved_variant_under_parent_id_with_ref() -> None:
    w = _World("q1")
    w.variant("v1")
    got = w.resolve()
    assert got.question.stable_id == "q1"
    assert got.variant_ref == "v1@c1"
    assert len(got.question.options) == 3


def test_non_servable_variants_are_never_served() -> None:
    w = _World("q1")
    for i, state in enumerate(
        [S.PENDING_REVIEW, S.WITHDRAWN, S.REJECTED, S.FLAGGED, S.GENERATED, S.VERIFIED]
    ):
        w.variants.add(NewVariant(f"x{i}", "q1", "trk", state))
        w.variants.add_revision(
            NewRevision(f"x{i}", "c1", "{}", state=RevisionState.PENDING_REVIEW)
        )
    assert w.resolve().variant_ref is None


def test_pick_is_deterministic_and_start_matches_submit() -> None:
    w = _World("q1")
    for name in ("v1", "v2", "v3"):
        w.variant(name)
    first = w.resolve()
    assert w.resolve() == first
    assert w.resolve(variant_ref=first.variant_ref) == first
    assert {w.resolve(due=_T0 + timedelta(days=d)).variant_ref for d in range(40)} == {
        "v1@c1",
        "v2@c1",
        "v3@c1",
    }


def test_pinned_variant_is_kept_while_servable_and_dropped_when_flagged() -> None:
    w = _World("q1")
    w.variant("v1")
    w.variant("v2")
    assert w.resolve(variant_ref="v2@c1").variant_ref == "v2@c1"
    w.flag(ItemKind.VARIANT, "v2")
    assert w.resolve(variant_ref="v2@c1").variant_ref == "v1@c1"
    assert w.resolve(variant_ref="garbage").variant_ref == "v1@c1"


def test_emptied_pool_falls_back_to_parent() -> None:
    w = _World("q1", "q2")
    w.variant("v1")
    w.flag(ItemKind.VARIANT, "v1")
    got = w.resolve()
    assert (got.question.stable_id, got.variant_ref) == ("q1", None)


def test_flagged_parent_serves_neither_parent_nor_variants() -> None:
    w = _World("q1", "q2", "q3")
    w.variant("v1")
    w.flag(ItemKind.QUESTION, "q1")
    got = w.resolve()
    assert got.question.stable_id in {"q2", "q3"}
    assert got.variant_ref is None


def test_flagged_parent_without_alternative_has_no_servable_question() -> None:
    w = _World("q1", "q2")
    w.variant("v1")
    w.flag(ItemKind.QUESTION, "q1")
    w.flag(ItemKind.QUESTION, "q2")
    with pytest.raises(NoServableQuestionError):
        w.resolve()


def test_quiz_and_pool_listing_exclude_flagged_questions_and_never_use_variants() -> None:
    w = _World("q1", "q2")
    w.variant("v1")
    w.flag(ItemKind.QUESTION, "q2")
    quiz = w.catalog.get_quiz("quiz")
    assert quiz is not None
    assert [q.stable_id for q in quiz.questions] == ["q1"]
    assert [q.stable_id for q in w.catalog.list_questions_for_concept(CONCEPT)] == ["q1"]


def test_serving_has_no_llm_provider_dependency() -> None:
    for module in (serving_module, servable_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        imports = [ln for ln in source.splitlines() if ln.lstrip().startswith(("import ", "from "))]
        assert not [ln for ln in imports if "llm" in ln.lower()]
