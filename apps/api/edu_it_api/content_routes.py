"""The Content_Router: quiz and project read routes under ``/v1`` (api-route-wiring Req 10).

``GET /quizzes/{stable_id}`` and ``GET /projects/{stable_id}`` require an
authenticated Session (resolved by the shared
:class:`~edu_it_api.identity.SessionUserResolver`) and a ``locale`` query parameter
of ``fr`` or ``en`` (default ``en``). Locale fallback to English is done by the
use cases. The quiz response has no field that reveals the correct option
(Req 10.2). A missing or archived item raises
:class:`~edu_it_application.learning.errors.NotFoundError`, rendered as a 404
Problem_Details by :mod:`edu_it_api.errors.learning`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.learning import GetProjectUseCase, GetQuizUseCase

__all__ = [
    "ContentServices",
    "ProjectResponse",
    "QuizOptionResponse",
    "QuizQuestionResponse",
    "QuizResponse",
    "RubricCriterionResponse",
    "build_content_router",
]


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


class QuizOptionResponse(_Response):
    """A selectable option: identifier and text only."""

    option_id: int
    text: str


class QuizQuestionResponse(_Response):
    """A question with its localized prompt, options, and hints."""

    stable_id: str
    prompt: str
    options: tuple[QuizOptionResponse, ...]
    hints: tuple[str, ...]


class QuizResponse(_Response):
    """A quiz in one locale."""

    stable_id: str
    title: str
    content_version: str
    locale: str
    questions: tuple[QuizQuestionResponse, ...]


class RubricCriterionResponse(_Response):
    """A rubric criterion with its localized title and weight."""

    stable_id: str
    title: str
    max_points: int


class ProjectResponse(_Response):
    """A project in one locale."""

    stable_id: str
    title: str
    description: str
    locale: str
    rubric: tuple[RubricCriterionResponse, ...]


@dataclass(frozen=True, slots=True)
class ContentServices:
    """The read use cases and identity resolver the content router needs."""

    current_user: Callable[[Request], str]
    get_quiz: GetQuizUseCase
    get_project: GetProjectUseCase


_StableId = Annotated[str, Path(min_length=1, max_length=128)]
_Locale = Annotated[Literal["fr", "en"], Query()]


def build_content_router(services: ContentServices) -> APIRouter:
    """Build the content router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["content"])

    @router.get("/quizzes/{stable_id}", response_model=QuizResponse)
    def get_quiz(request: Request, stable_id: _StableId, locale: _Locale = "en") -> QuizResponse:
        """A quiz in ``locale`` (English fallback); never reveals correct options."""
        services.current_user(request)
        view = services.get_quiz.execute(stable_id, locale)
        return QuizResponse(
            stable_id=view.stable_id,
            title=view.title,
            content_version=view.content_version,
            locale=view.locale,
            questions=tuple(
                QuizQuestionResponse(
                    stable_id=q.stable_id,
                    prompt=q.prompt,
                    options=tuple(
                        QuizOptionResponse(option_id=o.option_id, text=o.text) for o in q.options
                    ),
                    hints=q.hints,
                )
                for q in view.questions
            ),
        )

    @router.get("/projects/{stable_id}", response_model=ProjectResponse)
    def get_project(
        request: Request, stable_id: _StableId, locale: _Locale = "en"
    ) -> ProjectResponse:
        """A project and its rubric in ``locale`` (English fallback)."""
        services.current_user(request)
        view = services.get_project.execute(stable_id, locale)
        return ProjectResponse(
            stable_id=view.stable_id,
            title=view.title,
            description=view.description,
            locale=view.locale,
            rubric=tuple(
                RubricCriterionResponse(
                    stable_id=c.stable_id, title=c.title, max_points=c.max_points
                )
                for c in view.rubric
            ),
        )

    return router
