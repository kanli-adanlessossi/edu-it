"""Map reviewer-queue errors to RFC 9457 problems (ai-content Req 15.2, 15.8, 15.10).

``ReviewerExpertiseRequiredError`` renders 403 ``reviewer-expertise-required``,
``ReviewTaskNotFoundError`` 404 ``review-task-not-found``, the domain's
``ReviewTaskAlreadyDecidedError`` 409 ``review-task-already-decided`` and
``InvalidReviewActionError`` 422 ``invalid-review-action``. Titles and details are
translated (FR/EN); the exception message, which may name internal ids, is only
logged and never placed in the response.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from edu_it_application.ai.errors import (
    InvalidReviewActionError,
    RecommendationNotFoundError,
    ReviewerExpertiseRequiredError,
    ReviewTaskNotFoundError,
)
from edu_it_application.ai.submit_problem_report import InvalidProblemReportError
from edu_it_domain import config
from edu_it_domain.ai.serving import NoServableQuestionError
from edu_it_domain.ports.review_tasks import ReviewTaskAlreadyDecidedError

from edu_it_api.errors.catalog import ProblemType
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.problem import build_problem

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = [
    "INVALID_PROBLEM_REPORT",
    "INVALID_REVIEW_ACTION",
    "NO_SERVABLE_QUESTION",
    "RECOMMENDATION_NOT_FOUND",
    "REVIEWER_EXPERTISE_REQUIRED",
    "REVIEW_TASK_ALREADY_DECIDED",
    "REVIEW_TASK_NOT_FOUND",
    "ai_problem_response",
    "install_ai_error_handlers",
]

_LOGGER = logging.getLogger("edu_it_api.errors.ai")


def _type(
    status: HTTPStatus, slug: str, title: tuple[str, str], detail: tuple[str, str]
) -> ProblemType:
    return ProblemType(
        slug=slug,
        status=int(status),
        title={"fr": title[0], "en": title[1]},
        detail={"fr": detail[0], "en": detail[1]},
    )


REVIEWER_EXPERTISE_REQUIRED: Final[ProblemType] = _type(
    HTTPStatus.FORBIDDEN,
    "reviewer-expertise-required",
    ("Expertise de relecture requise", "Reviewer expertise required"),
    (
        "Seul un relecteur expert du parcours concerné peut consulter ou traiter cette tâche.",
        "Only a reviewer with expertise in this track can view or act on this task.",
    ),
)

REVIEW_TASK_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "review-task-not-found",
    ("Tâche de relecture introuvable", "Review task not found"),
    (
        "Cette tâche de relecture est introuvable. Vérifiez l'identifiant.",
        "This review task could not be found. Check the identifier.",
    ),
)

REVIEW_TASK_ALREADY_DECIDED: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "review-task-already-decided",
    ("Tâche déjà traitée", "Review task already decided"),
    (
        "Cette tâche de relecture a déjà fait l'objet d'une décision. Rien n'a été modifié.",
        "This review task has already been decided. Nothing was changed.",
    ),
)

INVALID_REVIEW_ACTION: Final[ProblemType] = _type(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid-review-action",
    ("Action de relecture invalide", "Invalid review action"),
    (
        "{reason} Corrigez la demande et réessayez ; rien n'a été modifié.",
        "{reason} Fix the request and try again; nothing was changed.",
    ),
)

INVALID_PROBLEM_REPORT: Final[ProblemType] = _type(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "invalid-problem-report",
    ("Signalement invalide", "Invalid problem report"),
    (
        "{reason} Corrigez le signalement et réessayez ; rien n'a été enregistré.",
        "{reason} Fix the report and try again; nothing was stored.",
    ),
)

#: Per-reason, per-locale text for ``InvalidProblemReportError``.
_REPORT_REASONS: Final[dict[str, dict[str, str]]] = {
    "comment_too_long": {
        "fr": "Le commentaire dépasse {limit} caractères.",
        "en": "The comment is longer than {limit} characters.",
    },
    "invalid_category": {
        "fr": "La catégorie doit être l'une de : {categories}.",
        "en": "The category must be one of: {categories}.",
    },
}

RECOMMENDATION_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "recommendation-not-found",
    ("Recommandation introuvable", "Recommendation not found"),
    (
        "Cette recommandation est introuvable. Vérifiez l'identifiant.",
        "This recommendation could not be found. Check the identifier.",
    ),
)

NO_SERVABLE_QUESTION: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "no-servable-question",
    ("Aucune question disponible", "No question available"),
    (
        "Aucune question approuvée n'est disponible pour ce concept pour le moment. "
        "Réessayez plus tard.",
        "No approved question is available for this concept right now. Try again later.",
    ),
)

#: Per-reason, per-locale text for ``InvalidReviewActionError``.
_REASONS: Final[dict[str, dict[str, str]]] = {
    "comment_required": {
        "fr": "Un commentaire est requis pour demander des modifications.",
        "en": "A comment is required to request changes.",
    },
    "body_invalid": {
        "fr": "Le contenu modifié est invalide.",
        "en": "The edited content is invalid.",
    },
    "unsupported_task": {
        "fr": "Cette action ne s'applique pas à ce type de tâche.",
        "en": "This action does not apply to this kind of task.",
    },
    "unknown_revision": {
        "fr": "La révision de cette tâche est introuvable.",
        "en": "The revision of this task could not be found.",
    },
    "unchanged_body": {
        "fr": "Le contenu est identique à une révision existante.",
        "en": "The content is identical to an existing revision.",
    },
}
_DEFAULT_REASON: Final[dict[str, str]] = {
    "fr": "L'action demandée est invalide.",
    "en": "The requested action is invalid.",
}


def _classify(exc: Exception, locale: str) -> tuple[ProblemType, Mapping[str, str]] | None:
    """Return ``(problem type, detail params)`` for ``exc``, or ``None``."""
    if isinstance(exc, ReviewerExpertiseRequiredError):
        return REVIEWER_EXPERTISE_REQUIRED, {}
    if isinstance(exc, ReviewTaskNotFoundError):
        return REVIEW_TASK_NOT_FOUND, {}
    if isinstance(exc, RecommendationNotFoundError):
        return RECOMMENDATION_NOT_FOUND, {}
    if isinstance(exc, NoServableQuestionError):
        return NO_SERVABLE_QUESTION, {}
    if isinstance(exc, ReviewTaskAlreadyDecidedError):
        return REVIEW_TASK_ALREADY_DECIDED, {}
    if isinstance(exc, InvalidProblemReportError):
        report_texts = _REPORT_REASONS.get(exc.reason, _REPORT_REASONS["invalid_category"])
        text = report_texts.get(locale, report_texts["en"]).format(
            limit=config.PROBLEM_REPORT_COMMENT_MAX_CHARS,
            categories=", ".join(config.PROBLEM_REPORT_CATEGORIES),
        )
        return INVALID_PROBLEM_REPORT, {"reason": text}
    if isinstance(exc, InvalidReviewActionError):
        texts = _REASONS.get(exc.reason, _DEFAULT_REASON)
        return INVALID_REVIEW_ACTION, {"reason": texts.get(locale, texts["en"])}
    return None


def ai_problem_response(request: Request, exc: Exception) -> Response | None:
    """Render ``exc`` as a translated problem, or ``None`` if it is not a mapped error."""
    locale = request_locale(request)
    classified = _classify(exc, locale)
    if classified is None:
        return None
    problem_type, params = classified
    _LOGGER.info(
        "review error %s serving %s %s: %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc,
    )
    return problem_response(
        build_problem(
            status=problem_type.status,
            locale=locale,
            problem_type=problem_type,
            instance=request.url.path,
            detail_params=params,
        )
    )


async def _handle_ai_error(request: Request, exc: Exception) -> Response:
    """Exception handler for the reviewer queue error family."""
    response = ai_problem_response(request, exc)
    if response is not None:
        return response
    _LOGGER.warning("unmapped review error %s", type(exc).__name__, exc_info=exc)
    return problem_response(
        build_problem(
            status=int(HTTPStatus.CONFLICT),
            locale=request_locale(request),
            instance=request.url.path,
        )
    )


def install_ai_error_handlers(app: FastAPI) -> None:
    """Register the reviewer queue error handlers on ``app``."""
    for error in (
        ReviewerExpertiseRequiredError,
        ReviewTaskNotFoundError,
        RecommendationNotFoundError,
        NoServableQuestionError,
        ReviewTaskAlreadyDecidedError,
        InvalidReviewActionError,
        InvalidProblemReportError,
    ):
        app.add_exception_handler(error, _handle_ai_error)
