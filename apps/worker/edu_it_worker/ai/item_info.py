"""``Catalog_ItemInfoSource``: the real ``ItemInfoSource`` of the quality loop (task 21.2).

Answers, for an item address, the track that owns it (for the Review_Task) and the
``content_version`` learners currently see (for the report count). It reads the catalog, the
lesson store, the lab catalog, the parent-question source and the variant store, and nothing
else. It imports no LLM port.

* ``variant``: the variant's track and its ``approved_content_version``.
* ``question``: the track of the parent question, and the version of the quiz that holds it.
  Learners report a question with their quiz's ``content_version`` (the quiz player sends it),
  so that is the version the report count must match. The content store names a question's
  concept like its quiz (``quiz.x`` and ``concept.x``), the convention
  ``Content_ParentQuestionSource`` documents; a question that quiz does not hold has no version.
* ``lesson``: the track of the lesson's module and the lesson's version.
* ``lab``: the track of the owning lesson's module and the lab's version.

An unknown item gives ``None``; an item with no known version gives ``content_version=None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from edu_it_domain.ports.item_flags import ItemKind
from edu_it_domain.ports.item_info import ItemInfo

if TYPE_CHECKING:
    from edu_it_domain.ports.catalog import CatalogRepository
    from edu_it_domain.ports.item_flags import ItemKey
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lessons import LessonRepository
    from edu_it_domain.ports.parent_questions import ParentQuestionSource
    from edu_it_domain.ports.variants import VariantRepository

__all__ = ["Catalog_ItemInfoSource"]

_CONCEPT_PREFIX: Final = "concept."
_QUIZ_PREFIX: Final = "quiz."


class Catalog_ItemInfoSource:
    """An :class:`ItemInfoSource` over the catalog, content and variant stores."""

    def __init__(
        self,
        *,
        catalog: CatalogRepository,
        lessons: LessonRepository,
        labs: LabCatalog,
        parents: ParentQuestionSource,
        variants: VariantRepository,
    ) -> None:
        """Wire the read ports."""
        self._catalog = catalog
        self._lessons = lessons
        self._labs = labs
        self._parents = parents
        self._variants = variants

    def describe(self, key: ItemKey) -> ItemInfo | None:
        """The item's track and current version, or ``None`` when the item is unknown."""
        match key.kind:
            case ItemKind.VARIANT:
                return self._variant(key.stable_id)
            case ItemKind.QUESTION:
                return self._question(key.stable_id)
            case ItemKind.LESSON:
                return self._lesson(key.stable_id)
            case ItemKind.LAB:
                return self._lab(key.stable_id)
        return None

    def _variant(self, stable_id: str) -> ItemInfo | None:
        variant = self._variants.get(stable_id)
        if variant is None:
            return None
        return ItemInfo(variant.track_stable_id, variant.approved_content_version)

    def _question(self, stable_id: str) -> ItemInfo | None:
        parent = self._parents.get(stable_id)
        if parent is None:
            return None
        return ItemInfo(
            parent.track_stable_id, self._quiz_version(parent.concept_stable_id, stable_id)
        )

    def _quiz_version(self, concept_id: str, question_id: str) -> str | None:
        if not concept_id.startswith(_CONCEPT_PREFIX):
            return None
        quiz = self._catalog.get_quiz(_QUIZ_PREFIX + concept_id.removeprefix(_CONCEPT_PREFIX))
        if quiz is None or not any(q.stable_id == question_id for q in quiz.questions):
            return None
        return quiz.content_version or None

    def _lesson(self, stable_id: str) -> ItemInfo | None:
        lesson = self._lessons.get_lesson(stable_id)
        if lesson is None:
            return None
        track = self._track_of_module(lesson.module_id)
        if track is None:
            return None
        return ItemInfo(track, lesson.content_version or None)

    def _lab(self, stable_id: str) -> ItemInfo | None:
        lab = self._labs.get(stable_id)
        if lab is None or not lab.lesson_id:
            return None
        lesson = self._lessons.get_lesson(lab.lesson_id)
        if lesson is None:
            return None
        track = self._track_of_module(lesson.module_id)
        if track is None:
            return None
        return ItemInfo(track, lab.content_version or None)

    def _track_of_module(self, module_id: str) -> str | None:
        module = self._catalog.get_module(module_id)
        return None if module is None else module.track_id
