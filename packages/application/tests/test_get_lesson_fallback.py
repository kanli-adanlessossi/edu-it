"""Tests for the English locale fallback in GetLessonUseCase (task 19.1).

**Validates: Requirements 17.3**
"""

from __future__ import annotations

import pytest
from edu_it_application.learning import GetLessonUseCase
from edu_it_application.learning.errors import NotFoundError
from edu_it_domain.ports.lessons import LessonRecord, LessonRepository


class _Lessons(LessonRepository):
    def __init__(self, *records: LessonRecord) -> None:
        self._items = {r.stable_id: r for r in records}

    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return self._items.get(stable_id)


def _lesson(stable_id: str, titles: dict[str, str], bodies: dict[str, str]) -> LessonRecord:
    return LessonRecord(stable_id=stable_id, module_id="mod-1", titles=titles, bodies=bodies)


def test_serves_requested_locale_when_translated() -> None:
    uc = GetLessonUseCase(
        lessons=_Lessons(_lesson("l", {"en": "T", "fr": "Titre"}, {"en": "e", "fr": "f"}))
    )
    view = uc.execute("l", "fr")
    assert (view.locale, view.body, view.title) == ("fr", "f", "Titre")


def test_falls_back_to_english_when_translation_missing() -> None:
    uc = GetLessonUseCase(lessons=_Lessons(_lesson("l", {"en": "T"}, {"en": "e"})))
    view = uc.execute("l", "fr")
    assert (view.locale, view.body, view.title) == ("en", "e", "T")


def test_fallback_to_english_for_unknown_locale() -> None:
    uc = GetLessonUseCase(lessons=_Lessons(_lesson("l", {"en": "T"}, {"en": "e"})))
    assert uc.execute("l", "de").locale == "en"


def test_missing_lesson_and_missing_english_raise_not_found() -> None:
    uc = GetLessonUseCase(lessons=_Lessons(_lesson("l", {"fr": "T"}, {"fr": "f"})))
    with pytest.raises(NotFoundError):
        uc.execute("nope", "en")
    with pytest.raises(NotFoundError):
        uc.execute("l", "en")
