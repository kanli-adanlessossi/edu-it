"""Learning routers under ``/v1`` (task 18.1, Req 17.1-17.4, 18.1, 18.3-18.5).

A thin HTTP boundary over the application use cases in
``edu_it_application.learning`` (``api -> application -> domain``): each route
parses a Pydantic v2 body, calls one use case, and maps the result to a response
model. All resources are addressed by ``stable_id`` and no internal numeric key
is exposed (Req 17.4).

Domain and application errors (:class:`~edu_it_application.learning.LearningError`,
``ForeignOptionError``) are deliberately *not* handled here; the RFC 9457 mapping
lives in the errors module (task 18.4).

The use cases and the authenticated-user resolver are injected through
:class:`LearningServices`, so the composition root decides the adapters and tests
drive the routes with in-memory fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    EnrollUseCase,
    GetLessonUseCase,
    GetProgressUseCase,
    RevealHintUseCase,
    StartReviewUseCase,
    SubmitProjectRequest,
    SubmitProjectUseCase,
    SubmitReviewRequest,
    SubmitReviewUseCase,
)
from edu_it_domain.learning.models import Mastery, QuizAttempt
from fastapi import APIRouter, Header, Query, Request, Response, status

from edu_it_api.learning_schemas import (
    AnswerResponse,
    EnrollmentRequest,
    EnrollmentResponse,
    HintRequest,
    HintResponse,
    LessonCompletionResponse,
    LessonResponse,
    MasteryResponse,
    ModuleProgressResponse,
    ProgressResponse,
    ProjectSubmissionRequest,
    ProjectSubmissionResponse,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizAttemptSummary,
    ReviewOptionResponse,
    ReviewQuestionResponse,
    ReviewResultResponse,
    ReviewSessionResponse,
    ReviewSubmissionRequest,
)

__all__ = [
    "CurrentUserResolver",
    "LearningServices",
    "build_learning_router",
]

CurrentUserResolver = Callable[[Request], str]
"""Resolve the authenticated learner's ``stable_id`` from a request."""


@dataclass(frozen=True, slots=True)
class LearningServices:
    """The use cases and identity resolver the learning router needs."""

    current_user: CurrentUserResolver
    enroll: EnrollUseCase
    progress: GetProgressUseCase
    lessons: GetLessonUseCase
    attempt_quiz: AttemptQuizUseCase
    reveal_hint: RevealHintUseCase
    start_review: StartReviewUseCase
    submit_review: SubmitReviewUseCase
    submit_project: SubmitProjectUseCase


def _mastery(m: Mastery) -> MasteryResponse:
    return MasteryResponse(concept_id=m.concept_id, score=m.score, state=m.state.value)


def _attempt(a: QuizAttempt) -> QuizAttemptSummary:
    return QuizAttemptSummary(
        stable_id=a.stable_id,
        quiz_id=a.quiz_id,
        score=a.score,
        is_best=a.is_best,
        attempted_at=a.attempted_at,
        content_version=a.content_version,
    )


def build_learning_router(services: LearningServices) -> APIRouter:
    """Build the learning router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["learning"])
    user_of = services.current_user

    @router.post(
        "/enrollments",
        response_model=EnrollmentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def enroll(body: EnrollmentRequest, request: Request, response: Response) -> EnrollmentResponse:
        """Enroll the learner in a track; re-enrolling returns the existing enrollment."""
        result = services.enroll.execute(user_of(request), body.track_id)
        if not result.created:
            response.status_code = status.HTTP_200_OK
        e = result.enrollment
        return EnrollmentResponse(
            stable_id=e.stable_id, track_id=e.track_id, enrolled_at=e.enrolled_at
        )

    @router.get("/progress", response_model=ProgressResponse)
    def progress(
        request: Request,
        track_id: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> ProgressResponse:
        """Module lock states, lesson completions, and mastery for a track."""
        view = services.progress.execute(user_of(request), track_id)
        return ProgressResponse(
            enrollment_id=view.enrollment_id,
            track_id=view.track_id,
            modules=[
                ModuleProgressResponse(
                    module_id=m.module_id,
                    ordinal=m.ordinal,
                    state=m.state,
                    tested_out=m.tested_out,
                    completions=[
                        LessonCompletionResponse(lesson_id=c.lesson_id, completed_at=c.completed_at)
                        for c in m.completions
                    ],
                )
                for m in view.modules
            ],
            mastery=[_mastery(m) for m in view.mastery],
        )

    @router.get("/lessons/{stable_id}", response_model=LessonResponse)
    def lesson(
        stable_id: str,
        locale: Annotated[str, Query(min_length=2, max_length=16)] = "en",
    ) -> LessonResponse:
        """A lesson in ``locale``, falling back to English (Req 17.3)."""
        view = services.lessons.execute(stable_id, locale)
        return LessonResponse(
            stable_id=view.stable_id,
            module_id=view.module_id,
            quiz_id=view.quiz_id,
            title=view.title,
            body=view.body,
            locale=view.locale,
            content_version=view.content_version,
        )

    @router.post("/quiz-attempts", response_model=QuizAttemptResponse)
    def quiz_attempt(
        body: QuizAttemptRequest,
        request: Request,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> QuizAttemptResponse:
        """Score a quiz submission; the ``Idempotency-Key`` header is required (Req 18.1)."""
        result = services.attempt_quiz.execute(
            AttemptQuizRequest(
                user_id=user_of(request),
                quiz_id=body.quiz_id,
                selections=body.selections,
                idempotency_key=idempotency_key,
                content_version=body.content_version,
            )
        )
        return QuizAttemptResponse(
            attempt=_attempt(result.attempt),
            best_attempt=_attempt(result.best_attempt),
            answers=[
                AnswerResponse(
                    question_id=a.question_id,
                    first_answered_at=a.first_answered_at,
                    counted_for_mastery=a.counted_for_mastery,
                )
                for a in result.answers
            ],
            mastery=[_mastery(m) for m in result.mastery],
            replayed=result.replayed,
        )

    @router.post("/hints", response_model=HintResponse)
    def hint(body: HintRequest, request: Request) -> HintResponse:
        """Reveal the next progressive hint level of a question (Req 18.3)."""
        result = services.reveal_hint.execute(user_of(request), body.quiz_id, body.question_id)
        return HintResponse(
            question_id=result.reveal.question_id,
            hint_level=result.hint_level,
            revealed_at=result.reveal.revealed_at,
            mastery=[_mastery(m) for m in result.mastery],
        )

    @router.get("/reviews", response_model=ReviewSessionResponse)
    def review_session(
        request: Request,
        concept_id: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> ReviewSessionResponse:
        """The questions of the concept's due review (Req 18.4)."""
        questions = services.start_review.execute(user_of(request), concept_id)
        return ReviewSessionResponse(
            concept_id=concept_id,
            questions=[
                ReviewQuestionResponse(
                    stable_id=q.stable_id,
                    options=[ReviewOptionResponse(option_id=o.option_id) for o in q.options],
                )
                for q in questions
            ],
        )

    @router.post("/reviews", response_model=ReviewResultResponse)
    def review_submit(body: ReviewSubmissionRequest, request: Request) -> ReviewResultResponse:
        """Submit answers to a due review (Req 18.4)."""
        result = services.submit_review.execute(
            SubmitReviewRequest(
                user_id=user_of(request),
                concept_id=body.concept_id,
                selections=body.selections,
            )
        )
        return ReviewResultResponse(
            concept_id=body.concept_id,
            passed=result.passed,
            score=result.score,
            due_at=result.schedule.due_at,
            interval_seconds=int(result.schedule.interval.total_seconds()),
            mastery=_mastery(result.mastery),
        )

    @router.post(
        "/project-submissions",
        response_model=ProjectSubmissionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def project_submission(
        body: ProjectSubmissionRequest, request: Request
    ) -> ProjectSubmissionResponse:
        """Submit a project; pending until the reviewer completes (Req 18.5)."""
        result = services.submit_project.execute(
            SubmitProjectRequest(
                user_id=user_of(request),
                project_id=body.project_id,
                self_assessment=body.self_assessment,
                evidence=body.evidence,
            )
        )
        s = result.submission
        return ProjectSubmissionResponse(
            stable_id=s.stable_id,
            project_id=s.project_id,
            status=result.status.value,
            review_outcome=s.review_outcome,
        )

    return router
