"""Pydantic v2 models for the problem report API (ai-content Req 18.2, 18.5).

The category and the comment length are checked by ``SubmitProblemReport`` against
``domain/config.py`` so the 422 problem names the rule in the request locale; the
schema only bounds the raw size so an absurd body is refused early.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ProblemReportRequest", "ProblemReportResponse"]

#: Hard cap on the raw comment before the use case applies the Config limit.
_RAW_COMMENT_CAP = 20_000


class ProblemReportRequest(BaseModel):
    """A report on a question, lesson or lab, for the version the learner saw."""

    model_config = ConfigDict(extra="forbid")

    item_kind: Literal["question", "variant", "lesson", "lab"]
    item_id: str = Field(min_length=1, max_length=128)
    content_version: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    comment: str | None = Field(default=None, max_length=_RAW_COMMENT_CAP)


class ProblemReportResponse(BaseModel):
    """The stored report; the comment is not echoed."""

    model_config = ConfigDict(frozen=True)

    stable_id: str
    item_kind: str
    item_id: str
    content_version: str
    category: str
    created_at: datetime
    replayed: bool
