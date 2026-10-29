"""Map learning domain/application errors to RFC 9457 problems (Req 18.6).

The learning use cases signal expected failures with typed exceptions
(:mod:`edu_it_application.learning.errors`) and the domain's
:class:`~edu_it_domain.learning.scoring.ForeignOptionError`. This module is the
boundary half of that contract: each exception type maps to a stable problem
type with a translated, actionable ``detail`` (FR/EN, request-locale rendered),
while the exception's own message -- a diagnostic that may name internal ids --
is only ever logged, never placed in the response (Req 18.6, 19.6).

Only the ``detail`` placeholders listed per type are filled, and only with safe,
learner-facing values (for example the instant a cooldown ends).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

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
from edu_it_domain.learning.scoring import ForeignOptionError

from edu_it_api.errors.catalog import ProblemType
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.pagination import install_pagination_error_handlers
from edu_it_api.errors.problem import build_problem

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = [
    "FOREIGN_OPTION",
    "IDEMPOTENCY_CONFLICT",
    "INSUFFICIENT_REVIEW_POOL",
    "INVALID_PROJECT_SUBMISSION",
    "NOT_ENROLLED",
    "QUIZ_IN_COOLDOWN",
    "REQUEST_IN_PROGRESS",
    "RESOURCE_NOT_FOUND",
    "REVIEW_NOT_AVAILABLE",
    "REVIEW_NOT_DUE",
    "install_learning_error_handlers",
    "learning_problem_response",
]

_LOGGER = logging.getLogger("edu_it_api.errors.learning")


def _type(
    status: HTTPStatus, slug: str, title: tuple[str, str], detail: tuple[str, str]
) -> ProblemType:
    return ProblemType(
        slug=slug,
        status=int(status),
        title={"fr": title[0], "en": title[1]},
        detail={"fr": detail[0], "en": detail[1]},
    )


FOREIGN_OPTION: Final[ProblemType] = _type(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "foreign-option",
    ("Réponse invalide", "Invalid answer"),
    (
        "Une réponse ne correspond pas à sa question. Rechargez le quiz et réessayez ; "
        "aucune tentative n'a été enregistrée.",
        "An answer does not belong to its question. Reload the quiz and try again; "
        "no attempt was recorded.",
    ),
)

QUIZ_IN_COOLDOWN: Final[ProblemType] = _type(
    HTTPStatus.TOO_MANY_REQUESTS,
    "quiz-in-cooldown",
    ("Quiz en période d'attente", "Quiz in cooldown"),
    (
        "Vous pourrez retenter ce quiz à partir du {attemptable_at} (UTC). "
        "Revoyez la leçon en attendant.",
        "You can retry this quiz from {attemptable_at} (UTC). Review the lesson in the meantime.",
    ),
)

INSUFFICIENT_REVIEW_POOL: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "insufficient-review-pool",
    ("Révision indisponible", "Review unavailable"),
    (
        "Il n'y a pas encore assez de questions pour réviser ce concept. "
        "Poursuivez votre parcours et réessayez plus tard.",
        "There are not enough questions to review this concept yet. "
        "Continue your track and try again later.",
    ),
)

RESOURCE_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "learning-resource-not-found",
    ("Ressource introuvable", "Not found"),
    (
        "Le contenu demandé est introuvable. Vérifiez l'identifiant et réessayez.",
        "The requested content could not be found. Check the identifier and try again.",
    ),
)

NOT_ENROLLED: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "not-enrolled",
    ("Inscription requise", "Enrollment required"),
    (
        "Vous devez d'abord vous inscrire à ce parcours.",
        "You need to enroll in this track first.",
    ),
)

IDEMPOTENCY_CONFLICT: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "idempotency-key-conflict",
    ("Clé d'idempotence déjà utilisée", "Idempotency key already used"),
    (
        "Cette clé d'idempotence a déjà servi pour une requête différente. "
        "Utilisez une nouvelle clé.",
        "This idempotency key was already used for a different request. Use a new key.",
    ),
)

REQUEST_IN_PROGRESS: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "request-in-progress",
    ("Requête en cours de traitement", "Request in progress"),
    (
        "Cette requête est encore en cours de traitement. Réessayez dans un instant.",
        "This request is still being processed. Try again in a moment.",
    ),
)

REVIEW_NOT_AVAILABLE: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "review-not-available",
    ("Révision indisponible", "Review unavailable"),
    (
        "Ce concept n'a pas encore de révision planifiée. Pratiquez-le d'abord.",
        "This concept has no review scheduled yet. Practice it first.",
    ),
)

REVIEW_NOT_DUE: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "review-not-due",
    ("Révision pas encore due", "Review not due yet"),
    (
        "Cette révision sera disponible à partir du {due_at} (UTC).",
        "This review will be available from {due_at} (UTC).",
    ),
)

INVALID_PROJECT_SUBMISSION: Final[ProblemType] = _type(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid-project-submission",
    ("Soumission de projet incomplète", "Incomplete project submission"),
    (
        "{reason} Complétez la soumission et renvoyez-la ; rien n'a été enregistré.",
        "{reason} Complete the submission and send it again; nothing was recorded.",
    ),
)

#: Per-reason, per-locale text for ``InvalidProjectSubmissionError`` (Req 20.1, 20.2).
_SUBMISSION_REASONS: Final[dict[str, dict[str, str]]] = {
    InvalidProjectSubmissionError.MISSING_SELF_ASSESSMENT: {
        "fr": "L'auto-évaluation est manquante.",
        "en": "The self-assessment is missing.",
    },
    InvalidProjectSubmissionError.INCOMPLETE_SELF_ASSESSMENT: {
        "fr": "Tous les critères de la grille doivent être auto-évalués.",
        "en": "Every rubric criterion must be self-assessed.",
    },
    InvalidProjectSubmissionError.MISSING_EVIDENCE: {
        "fr": "Une preuve de réalisation est requise.",
        "en": "Evidence of your work is required.",
    },
}
_DEFAULT_SUBMISSION_REASON: Final[dict[str, str]] = {
    "fr": "La soumission est incomplète.",
    "en": "The submission is incomplete.",
}


def _format_instant(value: datetime) -> str:
    """Render ``value`` as a minute-precision UTC timestamp (naive means UTC)."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.strftime("%Y-%m-%d %H:%M")


def _retry_after(value: datetime) -> str:
    """Render ``value`` as an HTTP-date for the ``Retry-After`` header."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return format_datetime(aware, usegmt=True)


def _classify(
    exc: Exception, locale: str
) -> tuple[ProblemType, Mapping[str, str], dict[str, str]] | None:
    """Return ``(problem type, detail params, extra headers)`` for ``exc``, or ``None``."""
    if isinstance(exc, ForeignOptionError):
        return FOREIGN_OPTION, {}, {}
    if isinstance(exc, QuizInCooldownError):
        return (
            QUIZ_IN_COOLDOWN,
            {"attemptable_at": _format_instant(exc.attemptable_at)},
            {"Retry-After": _retry_after(exc.attemptable_at)},
        )
    if isinstance(exc, InsufficientReviewPoolError):
        return INSUFFICIENT_REVIEW_POOL, {}, {}
    if isinstance(exc, NotFoundError):
        return RESOURCE_NOT_FOUND, {}, {}
    if isinstance(exc, NotEnrolledError):
        return NOT_ENROLLED, {}, {}
    if isinstance(exc, IdempotencyConflictError):
        return IDEMPOTENCY_CONFLICT, {}, {}
    if isinstance(exc, RequestInProgressError):
        return REQUEST_IN_PROGRESS, {}, {}
    if isinstance(exc, ReviewNotAvailableError):
        return REVIEW_NOT_AVAILABLE, {}, {}
    if isinstance(exc, ReviewNotDueError):
        return (
            REVIEW_NOT_DUE,
            {"due_at": _format_instant(exc.due_at)},
            {"Retry-After": _retry_after(exc.due_at)},
        )
    if isinstance(exc, InvalidProjectSubmissionError):
        texts = _SUBMISSION_REASONS.get(exc.reason, _DEFAULT_SUBMISSION_REASON)
        return INVALID_PROJECT_SUBMISSION, {"reason": texts.get(locale, texts["en"])}, {}
    return None


def learning_problem_response(request: Request, exc: Exception) -> Response | None:
    """Render ``exc`` as a translated problem, or ``None`` if it is not a learning error.

    The exception (with its internal-id-bearing message) is logged at ``info``
    level for diagnostics; the body carries only the catalog text (Req 18.6).
    """
    locale = request_locale(request)
    classified = _classify(exc, locale)
    if classified is None:
        return None
    problem_type, params, headers = classified
    _LOGGER.info(
        "learning error %s serving %s %s: %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc,
    )
    problem = build_problem(
        status=problem_type.status,
        locale=locale,
        problem_type=problem_type,
        instance=request.url.path,
        detail_params=params,
    )
    response = problem_response(problem)
    response.headers.update(headers)
    return response


async def _handle_learning_error(request: Request, exc: Exception) -> Response:
    """Exception handler for the learning error family."""
    response = learning_problem_response(request, exc)
    if response is not None:
        return response
    # An unmapped LearningError subclass: fall back to a safe generic conflict.
    _LOGGER.warning("unmapped learning error %s", type(exc).__name__, exc_info=exc)
    locale = request_locale(request)
    return problem_response(
        build_problem(status=int(HTTPStatus.CONFLICT), locale=locale, instance=request.url.path)
    )


def install_learning_error_handlers(app: FastAPI) -> None:
    """Register the learning error handlers on ``app`` (Req 18.6).

    Covers :class:`LearningError` and its subclasses plus the domain
    :class:`ForeignOptionError`; call after :func:`install_error_handlers`.
    """
    app.add_exception_handler(LearningError, _handle_learning_error)
    app.add_exception_handler(ForeignOptionError, _handle_learning_error)
    install_pagination_error_handlers(app)
