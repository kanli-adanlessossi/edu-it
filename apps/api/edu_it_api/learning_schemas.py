"""Pydantic v2 request/response models for the learning API (Req 17, 18).

Every resource is addressed by its opaque ``stable_id`` (Req 17.4): no model here
carries an internal numeric row key. The one integer identifier is a question
option's ``option_id``, which D3 defines as the stable per-question identity of
an option (it is how a selection names the option it chose), not a row key.

Response models never expose which option is correct.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AnswerResponse",
    "EnrollmentRequest",
    "EnrollmentResponse",
    "HintRequest",
    "HintResponse",
    "LessonCompletionResponse",
    "LessonResponse",
    "MasteryResponse",
    "ModuleProgressResponse",
    "ProgressResponse",
    "ProjectSubmissionRequest",
    "ProjectSubmissionResponse",
    "QuizAttemptRequest",
    "QuizAttemptResponse",
    "QuizAttemptSummary",
    "ReviewOptionResponse",
    "ReviewQuestionResponse",
    "ReviewResultResponse",
    "ReviewSessionResponse",
    "ReviewSubmissionRequest",
]

_ID = Field(min_length=1, max_length=128, description="Opaque stable_id.")


class _Request(BaseModel):
    """Base for request bodies: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    """Base for response bodies."""

    model_config = ConfigDict(frozen=True)


# --- enrollments -----------------------------------------------------------


class EnrollmentRequest(_Request):
    """Enroll in a track."""

    track_id: str = _ID


class EnrollmentResponse(_Response):
    """An enrollment."""

    stable_id: str
    track_id: str
    enrolled_at: datetime


# --- progress --------------------------------------------------------------


class LessonCompletionResponse(_Response):
    """A completed lesson."""

    lesson_id: str
    completed_at: datetime


class ModuleProgressResponse(_Response):
    """A module's lock state and completions."""

    module_id: str
    ordinal: int
    state: str
    tested_out: bool
    completions: list[LessonCompletionResponse]


class MasteryResponse(_Response):
    """A concept's mastery."""

    concept_id: str
    score: int
    state: str


class ProgressResponse(_Response):
    """A learner's progress through a track."""

    enrollment_id: str
    track_id: str
    modules: list[ModuleProgressResponse]
    mastery: list[MasteryResponse]


# --- lessons ---------------------------------------------------------------


class LessonResponse(_Response):
    """A lesson in the served locale."""

    stable_id: str
    module_id: str
    quiz_id: str | None
    title: str
    body: str
    locale: str
    content_version: str


# --- quiz attempts ---------------------------------------------------------


class QuizAttemptRequest(_Request):
    """A quiz submission."""

    quiz_id: str = _ID
    selections: dict[str, int | None] = Field(
        description="Question stable_id -> chosen option_id (null = unanswered)."
    )
    content_version: str | None = Field(
        default=None, description="Quiz version the attempt started under (pinning)."
    )


class QuizAttemptSummary(_Response):
    """A recorded attempt."""

    stable_id: str
    quiz_id: str
    score: int
    is_best: bool
    attempted_at: datetime
    content_version: str


class AnswerResponse(_Response):
    """A recorded answer."""

    question_id: str
    first_answered_at: datetime
    counted_for_mastery: bool


class QuizAttemptResponse(_Response):
    """The result of a quiz submission."""

    attempt: QuizAttemptSummary
    best_attempt: QuizAttemptSummary
    answers: list[AnswerResponse]
    mastery: list[MasteryResponse]
    replayed: bool


# --- hints -----------------------------------------------------------------


class HintRequest(_Request):
    """Reveal the next hint of a question."""

    quiz_id: str = _ID
    question_id: str = _ID


class HintResponse(_Response):
    """A revealed hint level."""

    question_id: str
    hint_level: int
    revealed_at: datetime
    mastery: list[MasteryResponse]


# --- reviews ---------------------------------------------------------------


class ReviewOptionResponse(_Response):
    """A selectable option; correctness is never revealed."""

    option_id: int


class ReviewQuestionResponse(_Response):
    """A review question."""

    stable_id: str
    options: list[ReviewOptionResponse]


class ReviewSessionResponse(_Response):
    """The questions of a due review."""

    concept_id: str
    questions: list[ReviewQuestionResponse]


class ReviewSubmissionRequest(_Request):
    """Answers to a due review."""

    concept_id: str = _ID
    selections: dict[str, int | None]


class ReviewResultResponse(_Response):
    """The recorded outcome of a review."""

    concept_id: str
    passed: bool
    score: int
    due_at: datetime
    interval_seconds: int
    mastery: MasteryResponse


# --- project submissions ---------------------------------------------------


class ProjectSubmissionRequest(_Request):
    """A project submission with rubric self-assessment and evidence."""

    project_id: str = _ID
    self_assessment: dict[str, int] = Field(
        default_factory=dict, description="Rubric criterion stable_id -> points."
    )
    evidence: list[str] = Field(default_factory=list)


class ProjectSubmissionResponse(_Response):
    """A recorded submission; ``review_outcome`` is null while pending."""

    stable_id: str
    project_id: str
    status: Literal["pending", "reviewed"]
    review_outcome: str | None
