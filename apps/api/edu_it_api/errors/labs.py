"""Map lab domain/application errors to RFC 9457 problems (task 7.1).

The lab use cases signal expected failures with typed exceptions
(:mod:`edu_it_application.labs.errors`) and the sandbox port's
:class:`~edu_it_domain.ports.sandbox.ProviderUnavailable`. Each maps to a stable
problem type with a translated (FR/EN) ``title`` and actionable ``detail``
(Req 14.7, 17.3, 25.2, 27.5, 30.1, 30.3, 30.4, 30.5). Throttling responses carry
``Retry-After`` in whole seconds. The exception's own message, which may name
internal ids, is only ever logged, never placed in the response (Req 19.6).

Unknown and foreign sessions render the same ``404`` (no existence leak).
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from edu_it_application.labs.errors import (
    ActiveSessionConflictError,
    GradingRejectedError,
    HintNotAvailableError,
    HintOutOfOrderError,
    LabError,
    LabFilesRejectedError,
    LabNotFoundError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
    LabSessionNotReadyError,
    LabTaskNotFoundError,
)
from edu_it_domain.labs.files import FileLimits, FileViolation
from edu_it_domain.labs.session_rules import RejectionReason
from edu_it_domain.ports.sandbox import ProviderUnavailable

from edu_it_api.errors.catalog import ProblemType
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.problem import build_problem

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = [
    "LAB_CAPACITY",
    "LAB_FILES_INVALID",
    "LAB_GRADING_IN_PROGRESS",
    "LAB_GRADING_THROTTLED",
    "LAB_HINT_NOT_AVAILABLE",
    "LAB_HINT_OUT_OF_ORDER",
    "LAB_MESSAGES",
    "LAB_NOT_FOUND",
    "LAB_PROVISION_FAILED",
    "LAB_PROVISION_RATE_LIMITED",
    "LAB_SANDBOX_UNAVAILABLE",
    "LAB_SESSION_ACTIVE",
    "LAB_SESSION_EXPIRED",
    "LAB_SESSION_INTERRUPTED",
    "LAB_SESSION_NOT_FOUND",
    "LAB_SESSION_NOT_READY",
    "LAB_TASK_NOT_FOUND",
    "install_lab_error_handlers",
    "lab_problem_response",
]

_LOGGER = logging.getLogger("edu_it_api.errors.labs")


def _type(
    status: HTTPStatus, slug: str, title: tuple[str, str], detail: tuple[str, str]
) -> ProblemType:
    return ProblemType(
        slug=slug,
        status=int(status),
        title={"fr": title[0], "en": title[1]},
        detail={"fr": detail[0], "en": detail[1]},
    )


LAB_SESSION_ACTIVE: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-session-active",
    ("Un lab est déjà en cours", "A lab is already running"),
    (
        "Le lab « {active_lab_id} » est déjà en cours. Terminez-le avant d'en démarrer un autre.",
        'The lab "{active_lab_id}" is already running. End it before starting another one.',
    ),
)

LAB_SESSION_ACTIVE_SAME_LAB: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-session-active",
    ("Un lab est déjà en cours", "A lab is already running"),
    (
        "Ce lab (« {active_lab_id} ») est déjà en cours. Reprenez-le ou terminez-le "
        "pour repartir de zéro.",
        'This lab ("{active_lab_id}") is already running. Resume it or end it to start over.',
    ),
)

LAB_PROVISION_RATE_LIMITED: Final[ProblemType] = _type(
    HTTPStatus.TOO_MANY_REQUESTS,
    "lab-provision-rate-limited",
    ("Trop de démarrages de lab", "Too many lab starts"),
    (
        "Vous avez démarré trop de labs récemment. Réessayez dans {retry_after} s.",
        "You have started too many labs recently. Try again in {retry_after} s.",
    ),
)

LAB_GRADING_THROTTLED: Final[ProblemType] = _type(
    HTTPStatus.TOO_MANY_REQUESTS,
    "lab-grading-throttled",
    ("Évaluation trop rapprochée", "Grading requested too soon"),
    (
        "Vous venez de demander une évaluation. Réessayez dans {retry_after} s.",
        "You just requested a grading run. Try again in {retry_after} s.",
    ),
)

LAB_GRADING_IN_PROGRESS: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-grading-in-progress",
    ("Évaluation en cours", "Grading in progress"),
    (
        "Une évaluation est déjà en cours pour cette session. Attendez son résultat.",
        "A grading run is already in progress for this session. Wait for its result.",
    ),
)

LAB_SESSION_EXPIRED: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-session-expired",
    ("Session de lab expirée", "Lab session expired"),
    (
        "Cette session de lab a expiré. Démarrez une nouvelle session pour poursuivre.",
        "This lab session has expired. Start a new session to continue.",
    ),
)

LAB_SESSION_NOT_READY: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-session-not-ready",
    ("Session de lab non prête", "Lab session not ready"),
    (
        "La session de lab n'est pas prête pour cette action. Attendez qu'elle soit prête "
        "ou démarrez-en une nouvelle.",
        "The lab session is not ready for this action. Wait until it is ready or start a new one.",
    ),
)

LAB_SESSION_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "lab-session-not-found",
    ("Session de lab introuvable", "Lab session not found"),
    (
        "Cette session de lab est introuvable. Vérifiez l'identifiant ou démarrez une session.",
        "This lab session could not be found. Check the identifier or start a session.",
    ),
)

LAB_FILES_INVALID: Final[ProblemType] = _type(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "lab-files-invalid",
    ("Fichiers du lab invalides", "Invalid lab files"),
    (
        "{reason} Corrigez vos fichiers et réenregistrez ; rien n'a été copié.",
        "{reason} Fix your files and save again; nothing was copied.",
    ),
)

LAB_SANDBOX_UNAVAILABLE: Final[ProblemType] = _type(
    HTTPStatus.SERVICE_UNAVAILABLE,
    "lab-sandbox-unavailable",
    ("Environnement de lab indisponible", "Lab environment unavailable"),
    (
        "L'environnement de lab est momentanément indisponible. Réessayez dans un instant ; "
        "aucune session n'a été créée.",
        "The lab environment is temporarily unavailable. Try again shortly; "
        "no session was created.",
    ),
)

LAB_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "lab-not-found",
    ("Lab introuvable", "Lab not found"),
    (
        "Ce lab est introuvable ou n'est plus disponible. Choisissez un autre lab.",
        "This lab could not be found or is no longer available. Choose another lab.",
    ),
)

LAB_TASK_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "lab-task-not-found",
    ("Tâche de lab introuvable", "Lab task not found"),
    (
        "Cette tâche ne fait pas partie de ce lab. Vérifiez l'identifiant de la tâche.",
        "This task is not part of this lab. Check the task identifier.",
    ),
)

LAB_HINT_NOT_AVAILABLE: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "lab-hint-not-available",
    ("Indice indisponible", "Hint not available"),
    (
        "Cet indice n'existe pas pour cette tâche.",
        "This hint does not exist for this task.",
    ),
)

LAB_HINT_OUT_OF_ORDER: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-hint-out-of-order",
    ("Indice demandé dans le désordre", "Hint requested out of order"),
    (
        "Révélez d'abord l'indice {next_level} de cette tâche avant de demander celui-ci.",
        "Reveal hint {next_level} of this task before requesting this one.",
    ),
)

#: Translated text shown on a ``failed`` session (Req 25.1); not an HTTP error response.
LAB_PROVISION_FAILED: Final[ProblemType] = _type(
    HTTPStatus.SERVICE_UNAVAILABLE,
    "lab-provision-failed",
    ("Le lab n'a pas pu démarrer", "The lab could not start"),
    (
        "Nous n'avons pas pu préparer votre environnement de lab. Démarrez un nouveau lab.",
        "We could not prepare your lab environment. Start a new lab.",
    ),
)

#: Translated text shown when the labs are at capacity (kubernetes-deployment Req 22.2).
#: Fixed text: it never carries quota figures or other Kubernetes output.
LAB_CAPACITY: Final[ProblemType] = _type(
    HTTPStatus.SERVICE_UNAVAILABLE,
    "lab-capacity",
    ("Les labs sont à pleine capacité", "Labs are at capacity"),
    (
        "Les labs sont à pleine capacité. Réessayez dans un instant.",
        "Labs are at capacity. Try again shortly.",
    ),
)

#: Translated text shown when a session was lost to an infrastructure event
#: (kubernetes-deployment Req 19, 20). No infrastructure detail.
LAB_SESSION_INTERRUPTED: Final[ProblemType] = _type(
    HTTPStatus.CONFLICT,
    "lab-session-interrupted",
    ("Session de lab interrompue", "Lab session interrupted"),
    (
        "Votre session de lab a été interrompue. Vos progrès sont conservés. "
        "Démarrez un nouveau lab pour continuer.",
        "Your lab session was interrupted. Your progress is kept. Start a new lab to continue.",
    ),
)

#: Message key -> FR/EN text for the learner-facing lab messages that are delivered by key.
LAB_MESSAGES: Final[dict[str, ProblemType]] = {
    "labs.capacity": LAB_CAPACITY,
    "labs.session_interrupted": LAB_SESSION_INTERRUPTED,
    "labs.provision_failed": LAB_PROVISION_FAILED,
}

_LIMITS: Final[FileLimits] = FileLimits()

#: Per-violation, per-locale reason text for ``lab-files-invalid`` (Req 14.7).
_FILE_REASONS: Final[dict[FileViolation, dict[str, str]]] = {
    FileViolation.TOO_MANY_FILES: {
        "fr": f"Vous ne pouvez pas enregistrer plus de {_LIMITS.max_count} fichiers.",
        "en": f"You cannot save more than {_LIMITS.max_count} files.",
    },
    FileViolation.FILE_TOO_LARGE: {
        "fr": f"Un fichier dépasse la taille maximale de {_LIMITS.max_bytes} octets.",
        "en": f"A file exceeds the maximum size of {_LIMITS.max_bytes} bytes.",
    },
    FileViolation.NAME_NOT_ALLOWED: {
        "fr": "Un nom de fichier n'est pas autorisé pour ce lab.",
        "en": "A file name is not allowed for this lab.",
    },
    FileViolation.UNSAFE_NAME: {
        "fr": "Un nom de fichier est invalide.",
        "en": "A file name is invalid.",
    },
}
_DEFAULT_FILE_REASON: Final[dict[str, str]] = {
    "fr": "Les fichiers sont invalides.",
    "en": "The files are invalid.",
}


def _retry_seconds(delta: timedelta | None) -> int:
    """Whole seconds to wait, rounded up and at least 1."""
    if delta is None:
        return 1
    return max(1, math.ceil(delta.total_seconds()))


def _classify(
    exc: Exception, locale: str
) -> tuple[ProblemType, Mapping[str, str], dict[str, str]] | None:
    """Return ``(problem type, detail params, extra headers)`` for ``exc``, or ``None``."""
    if isinstance(exc, ActiveSessionConflictError):
        problem = LAB_SESSION_ACTIVE_SAME_LAB if exc.same_lab else LAB_SESSION_ACTIVE
        return problem, {"active_lab_id": str(exc.active_lab_id)}, {}
    if isinstance(exc, LabProvisionRateLimitedError):
        seconds = _retry_seconds(exc.retry_after)
        return (
            LAB_PROVISION_RATE_LIMITED,
            {"retry_after": str(seconds)},
            {"Retry-After": str(seconds)},
        )
    if isinstance(exc, GradingRejectedError):
        if exc.reason is RejectionReason.THROTTLED:
            seconds = _retry_seconds(exc.retry_after)
            return (
                LAB_GRADING_THROTTLED,
                {"retry_after": str(seconds)},
                {"Retry-After": str(seconds)},
            )
        if exc.reason is RejectionReason.IN_PROGRESS:
            return LAB_GRADING_IN_PROGRESS, {}, {}
        if exc.reason is RejectionReason.EXPIRED:
            return LAB_SESSION_EXPIRED, {}, {}
        if exc.reason is RejectionReason.NOT_READY:
            return LAB_SESSION_NOT_READY, {}, {}
        return None
    if isinstance(exc, LabSessionNotReadyError):
        return LAB_SESSION_NOT_READY, {}, {}
    if isinstance(exc, LabSessionNotFoundError):
        return LAB_SESSION_NOT_FOUND, {}, {}
    if isinstance(exc, LabNotFoundError):
        return LAB_NOT_FOUND, {}, {}
    if isinstance(exc, LabTaskNotFoundError):
        return LAB_TASK_NOT_FOUND, {}, {}
    if isinstance(exc, HintNotAvailableError):
        return LAB_HINT_NOT_AVAILABLE, {}, {}
    if isinstance(exc, HintOutOfOrderError):
        return LAB_HINT_OUT_OF_ORDER, {"next_level": str(exc.next_level)}, {}
    if isinstance(exc, LabFilesRejectedError):
        texts = _FILE_REASONS.get(exc.violation, _DEFAULT_FILE_REASON)
        return LAB_FILES_INVALID, {"reason": texts.get(locale, texts["en"])}, {}
    if isinstance(exc, ProviderUnavailable):
        return LAB_SANDBOX_UNAVAILABLE, {}, {}
    return None


def lab_problem_response(request: Request, exc: Exception) -> Response | None:
    """Render ``exc`` as a translated problem, or ``None`` if it is not a mapped lab error.

    The exception (with its internal-id-bearing message) is logged at ``info``
    level; the body carries only the catalog text.
    """
    locale = request_locale(request)
    classified = _classify(exc, locale)
    if classified is None:
        return None
    problem_type, params, headers = classified
    _LOGGER.info(
        "lab error %s serving %s %s: %s",
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


async def _handle_lab_error(request: Request, exc: Exception) -> Response:
    """Exception handler for the lab error family."""
    response = lab_problem_response(request, exc)
    if response is not None:
        return response
    # An unmapped LabError subclass: fall back to a safe generic conflict.
    _LOGGER.warning("unmapped lab error %s", type(exc).__name__, exc_info=exc)
    return problem_response(
        build_problem(
            status=int(HTTPStatus.CONFLICT),
            locale=request_locale(request),
            instance=request.url.path,
        )
    )


def install_lab_error_handlers(app: FastAPI) -> None:
    """Register the lab error handlers on ``app``.

    Covers :class:`LabError` and its subclasses plus the sandbox
    :class:`ProviderUnavailable`; call after :func:`install_error_handlers`.
    """
    app.add_exception_handler(LabError, _handle_lab_error)
    app.add_exception_handler(ProviderUnavailable, _handle_lab_error)
