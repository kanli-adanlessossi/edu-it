"""Path recommendation router under ``/v1`` (ai-content Req 17.7, 17.8).

A thin HTTP boundary over ``GetRecommendation`` and ``DismissRecommendation``. Both routes
need a signed-in learner and the unsafe one passes through the existing CSRF gate. A
recommendation that is not the caller's is a 404 problem, rendered by
:mod:`edu_it_api.errors.ai`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response, status

from edu_it_api.path_recommendations_schemas import DismissalResponse, PathRecommendationResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.ai import DismissRecommendation, GetRecommendation

__all__ = ["PathRecommendationServices", "build_path_recommendations_router"]


@dataclass(frozen=True, slots=True)
class PathRecommendationServices:
    """The use cases and identity resolver the path recommendation router needs."""

    current_user: Callable[[Request], str]
    get: GetRecommendation
    dismiss: DismissRecommendation


def build_path_recommendations_router(services: PathRecommendationServices) -> APIRouter:
    """Build the path recommendation router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["path-recommendation"])

    @router.get(
        "/path-recommendation",
        response_model=PathRecommendationResponse,
        responses={status.HTTP_204_NO_CONTENT: {"description": "No recommendation to show."}},
    )
    def get_recommendation(request: Request) -> PathRecommendationResponse | Response:
        """The latest undismissed recommendation, or 204 when there is none."""
        found = services.get.execute(services.current_user(request))
        if found is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return PathRecommendationResponse(
            stable_id=found.stable_id,
            source=found.source,
            ordering=list(found.ordering),
            created_at=found.created_at,
        )

    @router.post(
        "/path-recommendation/{recommendation_id}/dismissal",
        response_model=DismissalResponse,
    )
    def dismiss(recommendation_id: str, request: Request) -> DismissalResponse:
        """Dismiss the caller's recommendation; repeating it changes nothing (200)."""
        result = services.dismiss.execute(services.current_user(request), recommendation_id)
        return DismissalResponse(stable_id=result.stable_id, dismissed_at=result.dismissed_at)

    return router
