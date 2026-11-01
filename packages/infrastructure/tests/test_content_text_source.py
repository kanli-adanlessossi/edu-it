"""Tests for ``Content_StoreTextSource`` over a content store (api-route-wiring task 6.1).

**Validates: Requirements 10.4, 10.5, 10.7**
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from edu_it_domain.ports.content_text import ContentTextSource
from edu_it_infrastructure.adapters.content_text import Content_StoreTextSource


@dataclass(frozen=True)
class _Item:
    stable_id: str
    payload: str
    status: str = "active"


class _Store:
    def __init__(self, items: dict[str, list[_Item]]) -> None:
        self._items = items

    def list_items(self, content_type: str) -> list[_Item]:
        return sorted(self._items.get(content_type, ()), key=lambda i: i.stable_id)


def _item(stable_id: str, payload: dict[str, object], status: str = "active") -> _Item:
    return _Item(stable_id, json.dumps(payload), status)


def _store() -> _Store:
    return _Store(
        {
            "quiz": [
                _item("qz_1", {"title": {"en": "Quiz", "fr": "Quiz FR"}}),
                _item("qz_old", {"title": {"en": "Old"}}, "archived"),
            ],
            "question": [
                _item(
                    "qn_1",
                    {"quiz_stable_id": "qz_1", "prompt_text": {"en": "Which?"}},
                ),
                _item("qn_other", {"quiz_stable_id": "qz_2", "prompt_text": {"en": "x"}}),
            ],
            "question_option": [
                _item(
                    "op_1",
                    {"question_stable_id": "qn_1", "is_correct": True, "text": {"en": "A"}},
                ),
                _item(
                    "op_2",
                    {
                        "question_stable_id": "qn_1",
                        "is_correct": False,
                        "text": {"en": "B", "fr": "B FR"},
                    },
                ),
            ],
            "project": [
                _item("pj_1", {"title": {"en": "Proj"}, "description": {"en": "Brief"}}),
            ],
            "rubric": [_item("rb_1", {"project_stable_id": "pj_1"})],
            "rubric_criterion": [
                _item("rc_1", {"rubric_stable_id": "rb_1", "title": {"en": "One", "fr": "Un"}}),
                _item("rc_2", {"rubric_stable_id": "rb_1", "title": {"en": "Two"}}),
            ],
        }
    )


def test_satisfies_port() -> None:
    assert isinstance(Content_StoreTextSource(_store()), ContentTextSource)


def test_quiz_text_falls_back_to_english_per_field() -> None:
    text = Content_StoreTextSource(_store()).quiz_text("qz_1", "fr")

    assert text is not None
    assert (text.locale, text.title) == ("fr", "Quiz FR")
    assert [q.question_id for q in text.questions] == ["qn_1"]
    question = text.questions[0]
    assert question.prompt == "Which?"  # no fr prompt: English
    assert question.option_texts == {"1": "A", "2": "B FR"}  # per field


def test_unsupported_locale_serves_english() -> None:
    text = Content_StoreTextSource(_store()).quiz_text("qz_1", "de")

    assert text is not None
    assert (text.locale, text.title) == ("en", "Quiz")


def test_project_text_lists_criterion_titles_in_order() -> None:
    text = Content_StoreTextSource(_store()).project_text("pj_1", "fr")

    assert text is not None
    assert (text.locale, text.title, text.brief) == ("en", "Proj", "Brief")
    assert text.criterion_titles == ("Un", "Two")


def test_unknown_and_archived_items_yield_none() -> None:
    source = Content_StoreTextSource(_store())

    assert source.quiz_text("missing", "en") is None
    assert source.quiz_text("qz_old", "en") is None
    assert source.project_text("missing", "en") is None
