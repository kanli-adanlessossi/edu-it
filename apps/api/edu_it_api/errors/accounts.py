"""Map account use-case failures to RFC 9457 problems (api-route-wiring Req 3, 4, 16.6).

Milestone 1 rows of the design's error table: sign-up validation (422 with a
``fields`` entry per rejected field), mail delivery failure (502
``mail-delivery-failed``), an invalid or expired verification link (400 with
``offer_resend``), the three sign-in refusals (401, 403, 429 with ``Retry-After``),
and :class:`~edu_it_api.identity.NotAuthenticated` (401).

Details are catalog text in fr and en. The exception's own message is logged at most,
never placed in a response, so no provider text, SQL, or secret reaches a body.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from edu_it_application.authenticate import SignInError, SignInFailure
from edu_it_application.gdpr import AccountDeletionError
from edu_it_application.resend_verification import ResendVerificationError
from edu_it_application.reset_password import PasswordResetMailDeliveryError
from edu_it_application.sign_up import SignUpMailDeliveryError, SignUpValidationError
from edu_it_domain.config import SIGN_IN_RATE_LIMIT_WINDOW
from fastapi.responses import JSONResponse

from edu_it_api.errors.catalog import VALIDATION, ProblemType
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.problem import (
    PROBLEM_JSON_MEDIA_TYPE,
    FieldError,
    ProblemDetails,
    build_problem,
)
from edu_it_api.identity import NotAuthenticated

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = [
    "DELETION_INCOMPLETE",
    "EMAIL_NOT_VERIFIED",
    "EXPORT_NOT_FOUND",
    "INVALID_CREDENTIALS",
    "MAIL_DELIVERY_FAILED",
    "NOT_AUTHENTICATED",
    "OAUTH_LINK_INVALID",
    "RESET_LINK_INVALID",
    "TOO_MANY_ATTEMPTS",
    "VERIFICATION_LINK_INVALID",
    "DeletionNotConfirmed",
    "ExportNotFound",
    "InvalidOAuthLink",
    "InvalidResetLink",
    "InvalidVerificationLink",
    "ResetPasswordPolicyError",
    "install_account_error_handlers",
]

_LOGGER = logging.getLogger("edu_it_api.errors.accounts")


def _type(
    status: HTTPStatus, slug: str, title: tuple[str, str], detail: tuple[str, str]
) -> ProblemType:
    return ProblemType(
        slug=slug,
        status=int(status),
        title={"fr": title[0], "en": title[1]},
        detail={"fr": detail[0], "en": detail[1]},
    )


MAIL_DELIVERY_FAILED: Final[ProblemType] = _type(
    HTTPStatus.BAD_GATEWAY,
    "mail-delivery-failed",
    ("Envoi du courriel impossible", "Email could not be sent"),
    (
        "Nous n'avons pas pu envoyer le courriel. Réessayez dans quelques instants.",
        "We could not send the email. Please try again in a few moments.",
    ),
)

VERIFICATION_LINK_INVALID: Final[ProblemType] = _type(
    HTTPStatus.BAD_REQUEST,
    "verification-link-invalid",
    ("Lien de vérification invalide", "Invalid verification link"),
    (
        "Ce lien de vérification est invalide ou a expiré. Demandez un nouveau courriel.",
        "This verification link is invalid or has expired. Request a new email.",
    ),
)

INVALID_CREDENTIALS: Final[ProblemType] = _type(
    HTTPStatus.UNAUTHORIZED,
    "invalid-credentials",
    ("Identifiants invalides", "Invalid credentials"),
    (
        "L'adresse courriel ou le mot de passe est incorrect.",
        "The email address or password is incorrect.",
    ),
)

EMAIL_NOT_VERIFIED: Final[ProblemType] = _type(
    HTTPStatus.FORBIDDEN,
    "email-not-verified",
    ("Courriel non vérifié", "Email not verified"),
    (
        "Vérifiez votre adresse courriel avant de vous connecter.",
        "Verify your email address before signing in.",
    ),
)

TOO_MANY_ATTEMPTS: Final[ProblemType] = _type(
    HTTPStatus.TOO_MANY_REQUESTS,
    "too-many-attempts",
    ("Trop de tentatives", "Too many attempts"),
    (
        "Trop de tentatives de connexion. Réessayez plus tard.",
        "Too many sign-in attempts. Try again later.",
    ),
)

NOT_AUTHENTICATED: Final[ProblemType] = _type(
    HTTPStatus.UNAUTHORIZED,
    "not-authenticated",
    ("Authentification requise", "Authentication required"),
    (
        "Connectez-vous pour continuer.",
        "Sign in to continue.",
    ),
)

_SIGN_IN_PROBLEMS: Final[dict[SignInFailure, ProblemType]] = {
    SignInFailure.INVALID_CREDENTIALS: INVALID_CREDENTIALS,
    SignInFailure.EMAIL_NOT_VERIFIED: EMAIL_NOT_VERIFIED,
    SignInFailure.RATE_LIMITED: TOO_MANY_ATTEMPTS,
}


RESET_LINK_INVALID: Final[ProblemType] = _type(
    HTTPStatus.BAD_REQUEST,
    "reset-link-invalid",
    ("Lien de réinitialisation invalide", "Invalid reset link"),
    (
        "Ce lien de réinitialisation est invalide ou a expiré. Demandez un nouveau lien.",
        "This reset link is invalid or has expired. Request a new link.",
    ),
)

OAUTH_LINK_INVALID: Final[ProblemType] = _type(
    HTTPStatus.BAD_REQUEST,
    "oauth-link-invalid",
    ("Liaison GitHub invalide", "Invalid GitHub link"),
    (
        "Cette confirmation de liaison est invalide ou a expiré. Recommencez la connexion.",
        "This link confirmation is invalid or has expired. Start the sign-in again.",
    ),
)


DELETION_INCOMPLETE: Final[ProblemType] = _type(
    HTTPStatus.INTERNAL_SERVER_ERROR,
    "deletion-incomplete",
    ("Suppression du compte incomplète", "Account deletion incomplete"),
    (
        "La suppression de votre compte n'a pas abouti et vos données sont inchangées. "
        "Réessayez plus tard.",
        "Your account deletion did not complete and your data is unchanged. Try again later.",
    ),
)

EXPORT_NOT_FOUND: Final[ProblemType] = _type(
    HTTPStatus.NOT_FOUND,
    "export-not-found",
    ("Export introuvable", "Export not found"),
    (
        "Aucun export de vos données n'est disponible. Demandez-en un d'abord.",
        "No export of your data is available. Request one first.",
    ),
)

#: Field location and reason reported when account deletion is not confirmed.
DELETION_CONFIRM_FIELD: Final = "body.confirm"
DELETION_CONFIRM_REASON: Final = "confirmation_required"


class DeletionNotConfirmed(Exception):
    """Raised by the deletion route when the body does not carry ``confirm: true``."""

    def __init__(self) -> None:
        super().__init__("account deletion was not confirmed")


class ExportNotFound(Exception):
    """Raised by the download route when the user has no stored export."""

    def __init__(self) -> None:
        super().__init__("no export stored for the user")


class InvalidOAuthLink(Exception):
    """Raised by the OAuth confirm route when the pending link is invalid or expired."""

    def __init__(self) -> None:
        super().__init__("oauth link confirmation invalid or expired")


#: Field location and reason reported when a reset password fails the policy.
RESET_PASSWORD_FIELD: Final = "body.new_password"
RESET_PASSWORD_REASON: Final = "password_policy_not_met"


class InvalidVerificationLink(Exception):
    """Raised by the verify-email route when the outcome is invalid or expired."""

    def __init__(self) -> None:
        super().__init__("verification link invalid or expired")


class InvalidResetLink(Exception):
    """Raised by the reset-completion route when the link is invalid, used, or expired."""

    def __init__(self) -> None:
        super().__init__("reset link invalid or expired")


class ResetPasswordPolicyError(Exception):
    """Raised by the reset-completion route when the new password fails the policy."""

    def __init__(self) -> None:
        super().__init__("reset password does not satisfy the policy")


def _render(
    request: Request,
    problem_type: ProblemType,
    *,
    headers: dict[str, str] | None = None,
    extension: dict[str, object] | None = None,
) -> Response:
    problem = build_problem(
        status=problem_type.status,
        locale=request_locale(request),
        problem_type=problem_type,
        instance=request.url.path,
    )
    if extension is None:
        response: Response = problem_response(problem)
    else:
        response = JSONResponse(
            status_code=problem.status,
            content={**problem.to_response_body(), **extension},
            media_type=PROBLEM_JSON_MEDIA_TYPE,
        )
    if headers:
        response.headers.update(headers)
    return response


async def _handle_sign_up_validation(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, SignUpValidationError)
    locale = request_locale(request)
    problem = build_problem(
        status=VALIDATION.status,
        locale=locale,
        problem_type=VALIDATION,
        instance=request.url.path,
    )
    fields = tuple(
        FieldError(location=f"body.{error.field}", reason=error.reason) for error in exc.errors
    )
    return problem_response(ProblemDetails(**{**problem.model_dump(), "fields": fields}))


async def _handle_mail_delivery(request: Request, exc: Exception) -> Response:
    _LOGGER.warning("mail delivery failed serving %s", request.url.path, exc_info=exc)
    return _render(request, MAIL_DELIVERY_FAILED)


async def _handle_invalid_link(request: Request, exc: Exception) -> Response:
    return _render(request, VERIFICATION_LINK_INVALID, extension={"offer_resend": True})


async def _handle_invalid_reset_link(request: Request, exc: Exception) -> Response:
    return _render(request, RESET_LINK_INVALID, extension={"offer_restart": True})


async def _handle_invalid_oauth_link(request: Request, exc: Exception) -> Response:
    return _render(request, OAUTH_LINK_INVALID)


async def _handle_reset_password_policy(request: Request, exc: Exception) -> Response:
    problem = build_problem(
        status=VALIDATION.status,
        locale=request_locale(request),
        problem_type=VALIDATION,
        instance=request.url.path,
    )
    fields = (FieldError(location=RESET_PASSWORD_FIELD, reason=RESET_PASSWORD_REASON),)
    return problem_response(ProblemDetails(**{**problem.model_dump(), "fields": fields}))


async def _handle_deletion_not_confirmed(request: Request, exc: Exception) -> Response:
    problem = build_problem(
        status=VALIDATION.status,
        locale=request_locale(request),
        problem_type=VALIDATION,
        instance=request.url.path,
    )
    fields = (FieldError(location=DELETION_CONFIRM_FIELD, reason=DELETION_CONFIRM_REASON),)
    return problem_response(ProblemDetails(**{**problem.model_dump(), "fields": fields}))


async def _handle_deletion_incomplete(request: Request, exc: Exception) -> Response:
    _LOGGER.error("account deletion did not complete serving %s", request.url.path, exc_info=exc)
    return _render(request, DELETION_INCOMPLETE)


async def _handle_export_not_found(request: Request, exc: Exception) -> Response:
    return _render(request, EXPORT_NOT_FOUND)


async def _handle_sign_in_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, SignInError)
    problem_type = _SIGN_IN_PROBLEMS[exc.failure]
    headers = None
    if exc.failure is SignInFailure.RATE_LIMITED:
        headers = {"Retry-After": str(int(SIGN_IN_RATE_LIMIT_WINDOW.total_seconds()))}
    return _render(request, problem_type, headers=headers)


async def _handle_not_authenticated(request: Request, exc: Exception) -> Response:
    return _render(request, NOT_AUTHENTICATED)


def install_account_error_handlers(app: FastAPI) -> None:
    """Register the account error handlers on ``app`` (Milestones 1 and 2)."""
    app.add_exception_handler(SignUpValidationError, _handle_sign_up_validation)
    app.add_exception_handler(SignUpMailDeliveryError, _handle_mail_delivery)
    app.add_exception_handler(ResendVerificationError, _handle_mail_delivery)
    app.add_exception_handler(PasswordResetMailDeliveryError, _handle_mail_delivery)
    app.add_exception_handler(InvalidVerificationLink, _handle_invalid_link)
    app.add_exception_handler(InvalidResetLink, _handle_invalid_reset_link)
    app.add_exception_handler(InvalidOAuthLink, _handle_invalid_oauth_link)
    app.add_exception_handler(ResetPasswordPolicyError, _handle_reset_password_policy)
    app.add_exception_handler(DeletionNotConfirmed, _handle_deletion_not_confirmed)
    app.add_exception_handler(AccountDeletionError, _handle_deletion_incomplete)
    app.add_exception_handler(ExportNotFound, _handle_export_not_found)
    app.add_exception_handler(SignInError, _handle_sign_in_error)
    app.add_exception_handler(NotAuthenticated, _handle_not_authenticated)
