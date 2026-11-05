"""Pydantic v2 request/response models for the labs API (Req 6.4, 14.2, 17.1-17.5).

Every resource is addressed by its opaque ``stable_id`` (Req 17.4); no model here
carries an internal numeric key. The only integers are counts and 1-based hint
levels, which are values rather than row keys. Hint text never appears in a lab
view: it is only returned by the hint endpoint once a level is granted (Req 14.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CheckResultResponse",
    "GradingRunEventResponse",
    "GradingRunResponse",
    "HintRequest",
    "HintResponse",
    "LabResponse",
    "LabSessionPageResponse",
    "LabSessionResponse",
    "LabTaskResponse",
    "SaveFilesRequest",
    "SaveFilesResponse",
    "SessionErrorResponse",
    "StartLabRequest",
]

_ID = Field(min_length=1, max_length=128, description="Opaque stable_id.")


class _Request(BaseModel):
    """Base for request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    """Base for response bodies."""

    model_config = ConfigDict(frozen=True)


class LabTaskResponse(_Response):
    """One task of a lab; ``hint_count`` only, never the hint text."""

    task_id: str
    required: bool
    text: str
    hint_count: int


class LabResponse(_Response):
    """A lab's instructions, tasks and starter file names in one locale."""

    lab_id: str
    kind: str
    title: str
    locale: str
    concept_ids: list[str]
    tasks: list[LabTaskResponse]
    learner_files: list[str]
    content_version: str = ""


class StartLabRequest(_Request):
    """Start a session of a lab."""

    lab_id: str = _ID


class SessionErrorResponse(_Response):
    """The translated, learner-facing reason a session ``failed``."""

    type: str
    title: str
    detail: str


class LabSessionResponse(_Response):
    """A lab session with the instants the Lab_View needs."""

    stable_id: str
    lab_id: str
    status: Literal[
        "provisioning",
        "ready",
        "grading",
        "ended",
        "expired",
        "idle-expired",
        "failed",
        "ended-by-infrastructure",
    ]
    created_at: datetime
    ttl_at: datetime
    idle_deadline: datetime
    grading_available_at: datetime | None
    ended_at: datetime | None
    server_now: datetime
    error: SessionErrorResponse | None = None


class LabSessionPageResponse(_Response):
    """A page of the caller's sessions."""

    items: list[LabSessionResponse]
    next_cursor: str | None


class SaveFilesRequest(_Request):
    """Learner files to copy into the sandbox workspace, by flat file name."""

    files: dict[str, str]


class SaveFilesResponse(_Response):
    """How many files were saved."""

    saved: int


class HintRequest(_Request):
    """Reveal one hint level of a task."""

    task_id: str = _ID
    level: int = Field(ge=1, le=1000, description="1-based hint level.")


class HintResponse(_Response):
    """A granted hint level with its text."""

    task_id: str
    level: int
    total_levels: int
    newly_revealed: bool
    text: str


class CheckResultResponse(_Response):
    """The learner-facing view of one Check result (Req 11.4, 18.3)."""

    task_id: str
    check_id: str
    outcome: Literal["passed", "failed", "could_not_complete"]
    feedback_key: str
    message: str


class GradingRunResponse(_Response):
    """A grading run with its learner-facing results so far."""

    stable_id: str
    session_id: str
    status: Literal["running", "passed", "failed", "could-not-complete"]
    accepted_at: datetime
    deadline_at: datetime
    started_at: datetime | None
    graded_at: datetime | None
    results: list[CheckResultResponse]


class GradingRunEventResponse(_Response):
    """Payload of the final ``run`` stream event."""

    stable_id: str
    session_id: str
    status: Literal["running", "passed", "failed", "could-not-complete"]
    graded_at: datetime | None
