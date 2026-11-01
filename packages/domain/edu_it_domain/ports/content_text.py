"""The ``ContentTextSource`` port: localized quiz and project text (Req 10.7, 16.1).

The catalog records carry structure only (identifiers, option ids, concept
links). The human-readable text lives in the synced content store. This
framework-free port gives the quiz and project read use cases that text for a
locale. Locale fallback to English is the source's responsibility, mirroring
``GetLessonUseCase``. An unknown or archived item yields ``None`` so the use
case can raise its own not-found error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "ContentTextSource",
    "ProjectText",
    "QuestionText",
    "QuizText",
]


@dataclass(frozen=True, slots=True)
class QuestionText:
    """The text of one quiz question and its options, in one locale."""

    question_id: str
    """The ``stable_id`` of the question."""

    prompt: str
    """The question prompt."""

    option_texts: dict[str, str] = field(default_factory=dict)
    """Option text keyed by the 1-based position of the option within its question.

    Options carry no ``stable_id`` in the catalog (their ``option_id`` is a row
    id), so the text is addressed by rank: the key ``"1"`` is the option with the
    lowest ``option_id`` of the question, ``"2"`` the next, and so on.
    """

    hints: tuple[str, ...] = ()
    """The progressive hints in reveal order (empty when the question has none)."""


@dataclass(frozen=True, slots=True)
class QuizText:
    """The text of a quiz as served in one locale."""

    locale: str
    """The locale actually served (may be the English fallback)."""

    title: str
    questions: tuple[QuestionText, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectText:
    """The text of a project as served in one locale."""

    locale: str
    """The locale actually served (may be the English fallback)."""

    title: str
    brief: str

    criterion_titles: tuple[str, ...] = ()
    """Rubric criterion titles in rubric order (position ``n`` is criterion ``n``)."""


@runtime_checkable
class ContentTextSource(Protocol):
    """Port that reads localized quiz and project text."""

    def quiz_text(self, quiz_id: str, locale: str) -> QuizText | None:
        """Return the quiz text in ``locale`` or English, or ``None`` if unknown."""
        ...

    def project_text(self, project_id: str, locale: str) -> ProjectText | None:
        """Return the project text in ``locale`` or English, or ``None`` if unknown."""
        ...
