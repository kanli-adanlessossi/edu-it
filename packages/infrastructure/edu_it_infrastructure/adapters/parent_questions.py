"""``Content_ParentQuestionSource``: the ``ParentQuestionSource`` adapter (ai-content Req 13.6).

Reads the synced ``question``, ``question_option``, ``quiz``, ``lesson``, ``module`` and
``concept`` items of the content store. A question is Approved when it is not archived and
either ``origin`` is ``human`` or a reviewer is recorded for it (``review_of``): the same rule
as design 14.1 for authored items.

The content schema has no question-to-concept field, so a question covers the concept named
like its quiz (``quiz.x`` -> ``concept.x``), the convention ``content sync`` uses when it writes
the catalog links. A question whose concept, quiz, lesson or module is missing is unknown to
this source, so no variant is drafted for it.

The store is typed structurally, as in ``Content_StoreTextSource``, so the infrastructure layer
does not import the content package. Options carry their text only: the answer key is never
copied into the ``ParentQuestion`` record.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Protocol

from edu_it_domain.ports.parent_questions import ParentQuestion

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from edu_it_infrastructure.adapters.content_text import TextContentItem

__all__ = ["Content_ParentQuestionSource", "ParentContentStore"]

_ARCHIVED: Final = "archived"
_HUMAN: Final = "human"
_LOCALES: Final = ("en", "fr")


class ParentContentStore(Protocol):
    """The slice of the content store this source reads."""

    def list_items(self, content_type: str) -> Sequence[TextContentItem]: ...

    def review_of(self, stable_id: str) -> tuple[str, datetime] | None: ...


def _is_archived(item: TextContentItem) -> bool:
    return str(getattr(item.status, "value", item.status)) == _ARCHIVED


def _localized(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


class Content_ParentQuestionSource:
    """A :class:`ParentQuestionSource` over the synced content store."""

    def __init__(self, store: ParentContentStore) -> None:
        self._store = store

    def approved_ids(self) -> list[str]:
        """Sorted ``stable_id`` of every Approved question that the source can describe."""
        parents = (self.get(item.stable_id) for item in self._store.list_items("question"))
        return sorted(p.stable_id for p in parents if p is not None and p.approved)

    def get(self, stable_id: str) -> ParentQuestion | None:
        """Return the question with its text, track and concept, or ``None`` when unknown."""
        question = self._find("question", stable_id)
        if question is None:
            return None
        payload, archived = question
        quiz_id = str(payload.get("quiz_stable_id", ""))
        context = self._context(quiz_id)
        if context is None:
            return None
        track_id, concept_id, concept_title = context
        prompts = _localized(payload.get("prompt_text"))
        if not all(prompts.get(locale) for locale in _LOCALES):
            return None
        return ParentQuestion(
            stable_id=stable_id,
            track_stable_id=track_id,
            concept_stable_id=concept_id,
            concept_title=concept_title,
            prompts=prompts,
            options=self._options(stable_id),
            approved=not archived and self._reviewed(stable_id, payload),
        )

    # -- internals ----------------------------------------------------------

    def _reviewed(self, stable_id: str, payload: dict[str, Any]) -> bool:
        provenance = payload.get("provenance")
        origin = provenance.get("origin") if isinstance(provenance, dict) else None
        if origin == _HUMAN:
            return True
        return self._store.review_of(stable_id) is not None

    def _find(self, content_type: str, stable_id: str) -> tuple[dict[str, Any], bool] | None:
        for item in self._store.list_items(content_type):
            if item.stable_id == stable_id:
                return json.loads(item.payload), _is_archived(item)
        return None

    def _options(self, question_id: str) -> tuple[str, ...]:
        texts: list[str] = []
        for item in self._store.list_items("question_option"):
            if _is_archived(item):
                continue
            payload = json.loads(item.payload)
            if str(payload.get("question_stable_id")) == question_id:
                texts.append(_localized(payload.get("text")).get("en", ""))
        return tuple(texts)

    def _context(self, quiz_id: str) -> tuple[str, str, str] | None:
        """``(track, concept, concept title)`` of the quiz, or ``None`` when a link is missing."""
        quiz = self._find("quiz", quiz_id)
        if quiz is None:
            return None
        lesson = self._find("lesson", str(quiz[0].get("lesson_stable_id", "")))
        if lesson is None:
            return None
        module = self._find("module", str(lesson[0].get("module_stable_id", "")))
        if module is None:
            return None
        concept_id = "concept." + quiz_id.split(".", 1)[-1]
        concept = self._find("concept", concept_id)
        if concept is None:
            return None
        title = _localized(concept[0].get("title")).get("en", "")
        return str(module[0].get("track_stable_id", "")), concept_id, title
