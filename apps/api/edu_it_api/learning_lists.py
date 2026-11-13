"""Cursor-paginated list routes under ``/v1`` (task 18.2, Req 17.5).

``GET /enrollments`` and ``GET /quiz-attempts`` page with an opaque ``cursor``
query parameter and a ``limit`` (never an offset). Each page carries
``next_cursor``, ``null`` on the last page. A malformed or expired cursor raises
:class:`~edu_it_application.learning.pagination.InvalidCursorError`, rendered as a
400 problem by :mod:`edu_it_api.errors.pagination`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from edu_it_domain.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict

from edu_it_api.learning_schemas import EnrollmentResponse, QuizAttemptSummary

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.learning import ListTracksUseCase
    from edu_it_application.learning.pagination import (
        ListEnrollmentsUseCase,
        ListQuizAttemptsUseCase,
    )

__all__ = [
    "EnrollmentPageResponse",
    "ListServices",
    "QuizAttemptPageResponse",
    "TrackPageResponse",
    "TrackResponse",
    "build_list_router",
]


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


class EnrollmentPageResponse(_Response):
    """A page of enrollments."""

    items: list[EnrollmentResponse]
    next_cursor: str | None


class QuizAttemptPageResponse(_Response):
    """A page of quiz attempts."""

    items: list[QuizAttemptSummary]
    next_cursor: str | None


class TrackResponse(_Response):
    """A track a learner can enroll in, with its localized title (empty when untitled)."""

    track_id: str
    title: str


class TrackPageResponse(_Response):
    """A page of tracks."""

    items: list[TrackResponse]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ListServices:
    """The list use cases and identity resolver the list router needs."""

    current_user: Callable[[Request], str]
    list_enrollments: ListEnrollmentsUseCase
    list_quiz_attempts: ListQuizAttemptsUseCase
    list_tracks: ListTracksUseCase | None = None
    """The track listing; `GET /tracks` is mounted only when it is provided."""


_Cursor = Annotated[str | None, Query(max_length=1024)]
_Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def build_list_router(services: ListServices) -> APIRouter:
    """Build the paginated list router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["learning"])

    @router.get("/enrollments", response_model=EnrollmentPageResponse)
    def list_enrollments(
        request: Request,
        cursor: _Cursor = None,
        limit: _Limit = DEFAULT_PAGE_SIZE,
    ) -> EnrollmentPageResponse:
        """The learner's enrollments, oldest first, one keyset page at a time."""
        page = services.list_enrollments.execute(
            services.current_user(request), cursor=cursor, limit=limit
        )
        return EnrollmentPageResponse(
            items=[
                EnrollmentResponse(
                    stable_id=e.stable_id, track_id=e.track_id, enrolled_at=e.enrolled_at
                )
                for e in page.items
            ],
            next_cursor=page.next_cursor,
        )

    @router.get("/quiz-attempts", response_model=QuizAttemptPageResponse)
    def list_quiz_attempts(
        request: Request,
        quiz_id: Annotated[str, Query(min_length=1, max_length=128)],
        cursor: _Cursor = None,
        limit: _Limit = DEFAULT_PAGE_SIZE,
    ) -> QuizAttemptPageResponse:
        """The learner's attempts at a quiz, oldest first, one keyset page at a time."""
        page = services.list_quiz_attempts.execute(
            services.current_user(request), quiz_id, cursor=cursor, limit=limit
        )
        return QuizAttemptPageResponse(
            items=[
                QuizAttemptSummary(
                    stable_id=a.stable_id,
                    quiz_id=a.quiz_id,
                    score=a.score,
                    is_best=a.is_best,
                    attempted_at=a.attempted_at,
                    content_version=a.content_version,
                )
                for a in page.items
            ],
            next_cursor=page.next_cursor,
        )

    list_tracks = services.list_tracks
    if list_tracks is not None:

        @router.get("/tracks", response_model=TrackPageResponse)
        def tracks(
            request: Request,
            locale: Annotated[str, Query(min_length=2, max_length=16)] = "en",
            cursor: _Cursor = None,
            limit: _Limit = DEFAULT_PAGE_SIZE,
        ) -> TrackPageResponse:
            """The catalog's tracks in ``stable_id`` order, titled in ``locale`` (or English)."""
            services.current_user(request)  # authenticated like every learning route
            page = list_tracks.execute(locale, cursor=cursor, limit=limit)
            return TrackPageResponse(
                items=[TrackResponse(track_id=v.track_id, title=v.title) for v in page.items],
                next_cursor=page.next_cursor,
            )

    return router
