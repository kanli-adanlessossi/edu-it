"""The get-lesson use case (Req 17.3).

Returns a lesson in the requested locale, serving English when no translation
exists for that locale rather than erroring (Req 17.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from edu_it_domain.learning.models import StableId
from edu_it_domain.ports.lessons import LessonRepository

from edu_it_application.learning.errors import NotFoundError

__all__ = [
    "FALLBACK_LOCALE",
    "GetLessonUseCase",
    "LessonView",
]

FALLBACK_LOCALE: Final = "en"
"""The locale served when the requested one has no translation (Req 17.3)."""


@dataclass(frozen=True, slots=True)
class LessonView:
    """A lesson rendered for one locale."""

    stable_id: StableId
    module_id: StableId
    quiz_id: StableId | None
    title: str
    body: str
    locale: str
    """The locale actually served (may be the fallback, not the requested one)."""

    content_version: str


class GetLessonUseCase:
    """Read a lesson in a locale, falling back to English."""

    def __init__(self, *, lessons: LessonRepository) -> None:
        """Wire the lesson port."""
        self._lessons = lessons

    def execute(self, stable_id: StableId, locale: str) -> LessonView:
        """Return the lesson in ``locale`` or English.

        Raises:
            NotFoundError: the lesson does not exist, or has neither the
                requested locale nor English.
        """
        lesson = self._lessons.get_lesson(stable_id)
        if lesson is None:
            raise NotFoundError("lesson", stable_id)
        served = locale if locale in lesson.bodies else FALLBACK_LOCALE
        if served not in lesson.bodies:
            raise NotFoundError("lesson", stable_id)
        return LessonView(
            stable_id=lesson.stable_id,
            module_id=lesson.module_id,
            quiz_id=lesson.quiz_id,
            title=lesson.titles.get(served, lesson.titles.get(FALLBACK_LOCALE, "")),
            body=lesson.bodies[served],
            locale=served,
            content_version=lesson.content_version,
        )
