"""Pydantic v2 request/response models for the reviewer queue API (ai-content Req 15).

Everything is addressed by ``stable_id``; no internal numeric key appears. The
variant text is returned as the stored JSON object (FR and EN), next to its
provenance and findings, so a reviewer can decide from one response (Req 15.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DecisionRequest",
    "EditRequest",
    "RequestChangesRequest",
    "ReviewTaskDetailResponse",
    "ReviewTaskPageResponse",
    "ReviewTaskResponse",
    "RevisionResponse",
    "VariantSummaryResponse",
]

COMMENT_MAX_CHARS = 2000


class _Request(BaseModel):
    """Base for request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    """Base for response bodies."""

    model_config = ConfigDict(frozen=True)


class DecisionRequest(_Request):
    """Optional comment attached to an approval or a rejection."""

    comment: str | None = Field(default=None, max_length=COMMENT_MAX_CHARS)


class RequestChangesRequest(_Request):
    """The reviewer's comment, required when requesting changes."""

    comment: str = Field(min_length=1, max_length=COMMENT_MAX_CHARS)


class EditRequest(_Request):
    """The edited variant, French and English, as a JSON object."""

    body: dict[str, Any] = Field(min_length=1)


class ReviewTaskResponse(_Response):
    """One review task."""

    stable_id: str
    kind: str
    target_kind: str
    target_stable_id: str
    track_id: str
    state: str
    created_at: datetime
    revision_content_version: str | None
    reason: str | None
    comment: str | None
    reviewer_id: str | None
    decided_at: datetime | None


class ReviewTaskPageResponse(_Response):
    """A page of open review tasks."""

    items: list[ReviewTaskResponse]
    next_cursor: str | None


class VariantSummaryResponse(_Response):
    """The variant a task is about."""

    stable_id: str
    parent_question_id: str
    track_id: str
    state: str
    approved_content_version: str | None


class RevisionResponse(_Response):
    """The revision under review, with provenance and findings."""

    content_version: str
    state: str
    body: Any
    origin: str | None
    model: str | None
    prompt_id: str | None
    prompt_version: str | None
    generation_date: datetime | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    findings: Any | None
    created_at: datetime


class ReviewTaskDetailResponse(_Response):
    """A task with its variant and revision."""

    task: ReviewTaskResponse
    variant: VariantSummaryResponse | None
    revision: RevisionResponse | None
