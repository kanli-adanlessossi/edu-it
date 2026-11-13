"""``Content_StoreTextSource``: the ``ContentTextSource`` adapter over the content store.

Reads the synced ``quiz``, ``question``, ``question_option``, ``project``, ``rubric``
and ``rubric_criterion`` items (api-route-wiring Req 10.7) and returns the localized
text of a quiz or project. Each text field falls back to English when the requested
locale lacks it (Learning core 17.3); the served ``locale`` is the requested one when
the item's title has it, otherwise English. An unknown or archived quiz or project
yields ``None``; archived questions, options and criteria are dropped.

The store is any object with ``list_items(content_type)`` returning items that expose
``stable_id``, ``status`` and the canonical JSON ``payload`` (the shape of
``edu_it_content.content_store.ContentStore``). It is typed structurally here so the
infrastructure layer does not import the content package, as ``Content_LabCatalog``
does.

Options and criteria are addressed by rank: ``option_texts`` is keyed by the 1-based
position of the option among its question's options in ``stable_id`` order, and
``criterion_titles`` lists criteria in ``stable_id`` order. The catalog adapter assigns
``option_id`` and criterion order in the same sequence when ``content sync`` writes
them. Correctness (``is_correct``) is never read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, Protocol

from edu_it_domain.ports.content_text import ProjectText, QuestionText, QuizText

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "Content_StoreTextSource",
    "Content_StoreTitleReader",
    "TextContentItem",
    "TextContentStore",
]

_ARCHIVED: Final = "archived"
_FALLBACK_LOCALE: Final = "en"


class TextContentItem(Protocol):
    """The slice of a stored content item the text source reads."""

    @property
    def stable_id(self) -> str: ...

    @property
    def status(self) -> object: ...

    @property
    def payload(self) -> str: ...


class TextContentStore(Protocol):
    """The slice of the content store the text source reads."""

    def list_items(self, content_type: str) -> Sequence[TextContentItem]: ...


def _is_archived(item: TextContentItem) -> bool:
    return str(getattr(item.status, "value", item.status)) == _ARCHIVED


def _localized(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _pick(texts: Mapping[str, str], locale: str) -> str:
    """Return the text in ``locale``, else English, else the empty string."""
    return texts.get(locale, texts.get(_FALLBACK_LOCALE, ""))


def _served(titles: Mapping[str, str], locale: str) -> str:
    return locale if locale in titles else _FALLBACK_LOCALE


class Content_StoreTextSource:
    """A :class:`ContentTextSource` that reads localized text from the content store."""

    def __init__(self, store: TextContentStore) -> None:
        self._store = store

    def quiz_text(self, quiz_id: str, locale: str) -> QuizText | None:
        quiz = self._active("quiz", quiz_id)
        if quiz is None:
            return None
        titles = _localized(quiz.get("title"))

        options_by_question: dict[str, list[dict[str, str]]] = {}
        for _, payload in self._active_all("question_option"):
            options_by_question.setdefault(str(payload["question_stable_id"]), []).append(
                _localized(payload.get("text"))
            )

        questions = tuple(
            QuestionText(
                question_id=question_id,
                prompt=_pick(_localized(payload.get("prompt_text")), locale),
                option_texts={
                    str(rank): _pick(text, locale)
                    for rank, text in enumerate(options_by_question.get(question_id, ()), start=1)
                },
                hints=tuple(_pick(_localized(h), locale) for h in payload.get("hints", ())),
            )
            for question_id, payload in self._active_all("question")
            if str(payload.get("quiz_stable_id")) == quiz_id
        )
        return QuizText(
            locale=_served(titles, locale), title=_pick(titles, locale), questions=questions
        )

    def project_text(self, project_id: str, locale: str) -> ProjectText | None:
        project = self._active("project", project_id)
        if project is None:
            return None
        titles = _localized(project.get("title"))

        rubric_ids = {
            rubric_id
            for rubric_id, payload in self._active_all("rubric")
            if str(payload.get("project_stable_id")) == project_id
        }
        criterion_titles = tuple(
            _pick(_localized(payload.get("title")), locale)
            for _, payload in self._active_all("rubric_criterion")
            if str(payload.get("rubric_stable_id")) in rubric_ids
        )
        return ProjectText(
            locale=_served(titles, locale),
            title=_pick(titles, locale),
            brief=_pick(_localized(project.get("description")), locale),
            criterion_titles=criterion_titles,
        )

    # -- internals ----------------------------------------------------------

    def _active_all(self, content_type: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (item.stable_id, json.loads(item.payload))
            for item in self._store.list_items(content_type)
            if not _is_archived(item)
        ]

    def _active(self, content_type: str, stable_id: str) -> dict[str, Any] | None:
        for item_id, payload in self._active_all(content_type):
            if item_id == stable_id:
                return payload
        return None


class Content_StoreTitleReader:
    """A ``CatalogTitleReader`` over the content store's ``title`` payload field.

    Reads the synced items of a content type once per call and skips archived ones,
    the same way :class:`Content_StoreTextSource` does.
    """

    def __init__(self, store: TextContentStore) -> None:
        self._store = store

    def titles(self, kind: str, stable_id: str) -> Mapping[str, str]:
        for item in self._store.list_items(kind):
            if item.stable_id == stable_id and not _is_archived(item):
                return _localized(json.loads(item.payload).get("title"))
        return {}

    def list_ids(self, kind: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.stable_id for item in self._store.list_items(kind) if not _is_archived(item)
            )
        )
