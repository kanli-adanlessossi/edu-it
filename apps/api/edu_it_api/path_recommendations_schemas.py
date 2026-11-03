"""Pydantic v2 models for the path recommendation API (ai-content Req 17.7, 17.8)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

__all__ = ["DismissalResponse", "PathRecommendationResponse"]


class PathRecommendationResponse(BaseModel):
    """The learner's latest undismissed recommendation: concept ids in suggested order."""

    model_config = ConfigDict(frozen=True)

    stable_id: str
    source: str
    ordering: list[str]
    created_at: datetime


class DismissalResponse(BaseModel):
    """A dismissed recommendation; repeating the dismissal returns the same state."""

    model_config = ConfigDict(frozen=True)

    stable_id: str
    dismissed_at: datetime
