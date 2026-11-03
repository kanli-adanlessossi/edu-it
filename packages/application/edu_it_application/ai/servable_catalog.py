"""``ServableCatalog``: the approved-only decorator over ``CatalogRepository``.

ai-content Requirements 14.1 to 14.3, 20.1, 20.3 to 20.5 and decision D4.

It excludes every item with an active ``item_flag`` (read through the
``ItemFlagReader``) and, for spaced reviews only, serves an Approved variant under
the parent's ``stable_id``. Graded quiz attempts never get variants. It has no
``LLMProvider`` dependency and depends on no Milestone C task: only the read side
of the flags is needed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from edu_it_domain.ai.serving import (
    NoServableQuestionError,
    ResolvedQuestion,
    ServableVariant,
    resolve_question,
    servable_variants,
    split_variant_ref,
)
from edu_it_domain.ports.item_flags import ItemKey, ItemKind

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from edu_it_domain.learning.models import Rubric, StableId
    from edu_it_domain.ports.catalog import (
        CatalogRepository,
        ConceptRecord,
        ModuleRecord,
        QuestionRecord,
        QuizRecord,
    )
    from edu_it_domain.ports.item_flags import ItemFlagReader
    from edu_it_domain.ports.variants import Variant, VariantRepository, VariantRevision

__all__ = ["NoServableQuestionError", "ResolvedQuestion", "ServableCatalog"]


def _question_key(stable_id: str) -> ItemKey:
    return ItemKey(ItemKind.QUESTION, stable_id)


class ServableCatalog:
    """A ``CatalogRepository`` that never returns a flagged question."""

    def __init__(
        self,
        catalog: CatalogRepository,
        flags: ItemFlagReader,
        variants: VariantRepository,
    ) -> None:
        self._catalog = catalog
        self._flags = flags
        self._variants = variants

    # -- CatalogRepository ------------------------------------------------

    def get_module(self, stable_id: StableId) -> ModuleRecord | None:
        return self._catalog.get_module(stable_id)

    def list_modules_for_track(self, track_id: StableId) -> tuple[ModuleRecord, ...]:
        return self._catalog.list_modules_for_track(track_id)

    def get_concept(self, stable_id: StableId) -> ConceptRecord | None:
        return self._catalog.get_concept(stable_id)

    def get_quiz(self, stable_id: StableId) -> QuizRecord | None:
        return self._without_flagged(self._catalog.get_quiz(stable_id))

    def get_quiz_at_version(self, stable_id: StableId, content_version: str) -> QuizRecord | None:
        return self._without_flagged(self._catalog.get_quiz_at_version(stable_id, content_version))

    def list_questions_for_concept(self, concept_id: StableId) -> tuple[QuestionRecord, ...]:
        questions = self._catalog.list_questions_for_concept(concept_id)
        flagged = self._flagged_questions(questions)
        return tuple(q for q in questions if q.stable_id not in flagged)

    def get_rubric_for_project(self, project_id: StableId) -> Rubric | None:
        return self._catalog.get_rubric_for_project(project_id)

    # -- Spaced review serving --------------------------------------------

    def resolve_review_question(
        self,
        *,
        concept_id: StableId,
        parent_question_id: StableId,
        user_ref: str,
        due_at: datetime,
        variant_ref: str | None = None,
    ) -> ResolvedQuestion:
        """The question a spaced review shows for ``parent_question_id``.

        Variant, then parent, then another question of the concept, else
        ``NoServableQuestionError``. When ``variant_ref`` is given (submit), the
        review stays on that variant as long as it is still servable.
        """
        raw = self._catalog.list_questions_for_concept(concept_id)
        flagged = self._flagged_questions(raw)
        parent = next((q for q in raw if q.stable_id == parent_question_id), None)
        parent_flagged = parent is None or parent.stable_id in flagged
        alternatives = [q for q in raw if q.stable_id not in flagged]
        pool: tuple[ServableVariant, ...] = ()
        pinned: ServableVariant | None = None
        if parent is not None and not parent_flagged:
            pool = self._pool(parent)
            pinned = self._pinned(parent, variant_ref)
        return resolve_question(
            concept_id=concept_id,
            parent=parent,
            parent_flagged=parent_flagged,
            pool=pool,
            alternatives=alternatives,
            user_ref=user_ref,
            due_at=due_at,
            pinned=pinned,
        )

    # -- helpers ----------------------------------------------------------

    def _flagged_questions(self, questions: Collection[QuestionRecord]) -> frozenset[str]:
        keys = [_question_key(q.stable_id) for q in questions]
        return frozenset(k.stable_id for k in self._flags.active_flags(keys))

    def _without_flagged(self, quiz: QuizRecord | None) -> QuizRecord | None:
        if quiz is None:
            return None
        flagged = self._flagged_questions(quiz.questions)
        if not flagged:
            return quiz
        return replace(
            quiz, questions=tuple(q for q in quiz.questions if q.stable_id not in flagged)
        )

    def _candidates(self, parent: QuestionRecord) -> list[tuple[Variant, VariantRevision]]:
        pairs: list[tuple[Variant, VariantRevision]] = []
        for revision in self._variants.list_servable(parent.stable_id):
            variant = self._variants.get(revision.variant_stable_id)
            if variant is not None:
                pairs.append((variant, revision))
        return pairs

    def _flagged_variants(self, variant_ids: Collection[str]) -> frozenset[ItemKey]:
        return self._flags.active_flags([ItemKey(ItemKind.VARIANT, v) for v in variant_ids])

    def _pool(self, parent: QuestionRecord) -> tuple[ServableVariant, ...]:
        pairs = self._candidates(parent)
        flagged = self._flagged_variants([variant.stable_id for variant, _ in pairs])
        return servable_variants(parent, pairs, flagged)

    def _pinned(self, parent: QuestionRecord, variant_ref: str | None) -> ServableVariant | None:
        """The variant the review started with, if it is still Approved and unflagged."""
        parts = split_variant_ref(variant_ref) if variant_ref else None
        if parts is None:
            return None
        variant = self._variants.get(parts[0])
        revision = self._variants.get_revision(*parts)
        if variant is None or revision is None:
            return None
        flagged = self._flagged_variants([variant.stable_id])
        pool = servable_variants(parent, [(variant, revision)], flagged)
        return pool[0] if pool else None
