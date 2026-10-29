"""Property test for the English locale fallback (task 19.2).

Feature: learning-core, Property 26: Lesson requests fall back to English

Drives the real ``GetLessonUseCase`` over an in-memory lesson repository.
"""

from __future__ import annotations

from edu_it_application.learning import GetLessonUseCase
from edu_it_domain.ports.lessons import LessonRecord, LessonRepository
from hypothesis import given, settings
from hypothesis import strategies as st

_LOCALES = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=2, max_size=8)
_TEXT = st.text(min_size=1, max_size=20)


class _Lessons(LessonRepository):
    def __init__(self, record: LessonRecord) -> None:
        self._record = record

    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return self._record if stable_id == self._record.stable_id else None


@st.composite
def _untranslated_request(draw: st.DrawFn) -> tuple[LessonRecord, str]:
    """A lesson that has English, plus a requested locale it lacks."""
    others = draw(st.sets(_LOCALES.filter(lambda x: x != "en"), max_size=4))
    locales = {"en", *others}
    bodies = {loc: draw(_TEXT) for loc in sorted(locales)}
    titles = {loc: draw(_TEXT) for loc in sorted(locales)}
    requested = draw(_LOCALES.filter(lambda x: x not in locales))
    record = LessonRecord(
        stable_id="lesson-1",
        module_id="mod-1",
        titles=titles,
        bodies=bodies,
        content_version="v1",
    )
    return record, requested


# Feature: learning-core, Property 26: Lesson requests fall back to English
# **Validates: Requirements 17.3**
@settings(max_examples=100, deadline=None)
@given(case=_untranslated_request())
def test_untranslated_lesson_is_served_in_english(case: tuple[LessonRecord, str]) -> None:
    record, requested = case
    view = GetLessonUseCase(lessons=_Lessons(record)).execute(record.stable_id, requested)

    assert view.locale == "en"
    assert view.body == record.bodies["en"]
    assert view.title == record.titles["en"]
    assert view.stable_id == record.stable_id
