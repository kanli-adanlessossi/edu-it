"""The quiz and project read use cases (api-route-wiring Req 10.1 to 10.7).

Structure (questions, options, rubric criteria) comes from the
:class:`~edu_it_domain.ports.catalog.CatalogRepository`; localized text comes from
the :class:`~edu_it_domain.ports.content_text.ContentTextSource`, matched by
``stable_id``. The views carry no field that reveals which option is correct
(Req 10.2): options are copied as ``option_id`` and text only. Locale fallback to
English is the text source's job, so ``locale`` on a view is the locale served.
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.learning.models import StableId
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.content_text import ContentTextSource

from edu_it_application.learning.errors import NotFoundError

__all__ = [
    "GetProjectUseCase",
    "GetQuizUseCase",
    "ProjectView",
    "QuizOptionView",
    "QuizQuestionView",
    "QuizView",
    "RubricCriterionView",
]


@dataclass(frozen=True, slots=True)
class QuizOptionView:
    """A selectable option: identifier and text only, never correctness."""

    option_id: int
    text: str


@dataclass(frozen=True, slots=True)
class QuizQuestionView:
    """A question with its localized prompt, options, and hints."""

    stable_id: StableId
    prompt: str
    options: tuple[QuizOptionView, ...]
    hints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuizView:
    """A quiz rendered for one locale."""

    stable_id: StableId
    title: str
    content_version: str
    locale: str
    """The locale actually served (may be the English fallback)."""

    questions: tuple[QuizQuestionView, ...]


@dataclass(frozen=True, slots=True)
class RubricCriterionView:
    """A rubric criterion with its localized title and weight."""

    stable_id: StableId
    title: str
    max_points: int


@dataclass(frozen=True, slots=True)
class ProjectView:
    """A project rendered for one locale."""

    stable_id: StableId
    title: str
    description: str
    locale: str
    """The locale actually served (may be the English fallback)."""

    rubric: tuple[RubricCriterionView, ...]


class GetQuizUseCase:
    """Read a quiz in a locale."""

    def __init__(self, *, catalog: CatalogRepository, text: ContentTextSource) -> None:
        """Wire the catalog and text ports."""
        self._catalog = catalog
        self._text = text

    def execute(self, quiz_id: StableId, locale: str) -> QuizView:
        """Return the quiz in ``locale`` or English.

        Raises:
            NotFoundError: the quiz is unknown, archived, or has no text.
        """
        quiz = self._catalog.get_quiz(quiz_id)
        text = self._text.quiz_text(quiz_id, locale)
        if quiz is None or text is None:
            raise NotFoundError("quiz", quiz_id)
        by_question = {q.question_id: q for q in text.questions}
        questions: list[QuizQuestionView] = []
        for question in quiz.questions:
            qtext = by_question.get(question.stable_id)
            ranked = sorted(question.options, key=lambda o: o.option_id)
            options = tuple(
                QuizOptionView(
                    option_id=option.option_id,
                    text=qtext.option_texts.get(str(rank), "") if qtext else "",
                )
                for rank, option in enumerate(ranked, start=1)
            )
            questions.append(
                QuizQuestionView(
                    stable_id=question.stable_id,
                    prompt=qtext.prompt if qtext else "",
                    options=options,
                    hints=tuple(qtext.hints) if qtext else (),
                )
            )
        return QuizView(
            stable_id=quiz.stable_id,
            title=text.title,
            content_version=quiz.content_version,
            locale=text.locale,
            questions=tuple(questions),
        )


class GetProjectUseCase:
    """Read a project in a locale."""

    def __init__(self, *, catalog: CatalogRepository, text: ContentTextSource) -> None:
        """Wire the catalog and text ports."""
        self._catalog = catalog
        self._text = text

    def execute(self, project_id: StableId, locale: str) -> ProjectView:
        """Return the project in ``locale`` or English.

        Raises:
            NotFoundError: the project is unknown, archived, or has no rubric.
        """
        rubric = self._catalog.get_rubric_for_project(project_id)
        text = self._text.project_text(project_id, locale)
        if rubric is None or text is None:
            raise NotFoundError("project", project_id)
        titles = text.criterion_titles
        criteria = tuple(
            RubricCriterionView(
                stable_id=criterion.stable_id,
                title=titles[position] if position < len(titles) else "",
                max_points=criterion.max_points,
            )
            for position, criterion in enumerate(rubric.criteria)
        )
        return ProjectView(
            stable_id=project_id,
            title=text.title,
            description=text.brief,
            locale=text.locale,
            rubric=criteria,
        )
