"""Learning use cases: the application layer over the domain services (Req 17, 18, 20).

Each use case loads state through the repository ports, calls the framework-free
domain services, and persists the results. They depend inward on
``edu_it_domain`` only (``api -> application -> domain <- infrastructure``), read
time through the injected ``Clock``, and take every business number from
``edu_it_domain.config``. Guards -- idempotency, cooldown, option validation,
submission completeness -- run before the first side effect.
"""

from edu_it_application.learning.attempt_quiz import (
    AttemptQuizRequest,
    AttemptQuizResult,
    AttemptQuizUseCase,
)
from edu_it_application.learning.errors import (
    IdempotencyConflictError,
    InsufficientReviewPoolError,
    InvalidProjectSubmissionError,
    LearningError,
    NotEnrolledError,
    NotFoundError,
    QuizInCooldownError,
    RequestInProgressError,
    ReviewNotAvailableError,
    ReviewNotDueError,
)
from edu_it_application.learning.get_lesson import GetLessonUseCase, LessonView
from edu_it_application.learning.progress import (
    EnrollResult,
    EnrollUseCase,
    GetProgressUseCase,
    ModuleProgressView,
    ProgressView,
)
from edu_it_application.learning.reveal_hint import RevealHintResult, RevealHintUseCase
from edu_it_application.learning.run_review import (
    StartReviewUseCase,
    SubmitReviewRequest,
    SubmitReviewResult,
    SubmitReviewUseCase,
)
from edu_it_application.learning.submit_project import (
    SubmissionStatus,
    SubmitProjectRequest,
    SubmitProjectResult,
    SubmitProjectUseCase,
)

__all__ = [
    "AttemptQuizRequest",
    "AttemptQuizResult",
    "AttemptQuizUseCase",
    "EnrollResult",
    "EnrollUseCase",
    "GetLessonUseCase",
    "GetProgressUseCase",
    "IdempotencyConflictError",
    "InsufficientReviewPoolError",
    "InvalidProjectSubmissionError",
    "LearningError",
    "LessonView",
    "ModuleProgressView",
    "NotEnrolledError",
    "NotFoundError",
    "ProgressView",
    "QuizInCooldownError",
    "RequestInProgressError",
    "RevealHintResult",
    "RevealHintUseCase",
    "ReviewNotAvailableError",
    "ReviewNotDueError",
    "StartReviewUseCase",
    "SubmissionStatus",
    "SubmitProjectRequest",
    "SubmitProjectResult",
    "SubmitProjectUseCase",
    "SubmitReviewRequest",
    "SubmitReviewResult",
    "SubmitReviewUseCase",
]
