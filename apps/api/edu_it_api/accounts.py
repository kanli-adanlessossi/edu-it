"""Milestone 1 account routes under ``/v1`` (api-route-wiring Req 2, 3, 4).

A thin HTTP boundary over the existing account use cases (``api -> application``):
each handler parses a strict Pydantic body, calls one use case, and calls one
cookie helper where a cookie changes. Use-case failures are rendered by
``errors/accounts.py``, never inline. A response that fails never sets the
Session_Cookie (Req 2.10): cookies are set only after the use case returns.

Sign-in sets a *new* Session_Cookie from the reference the use case created, never
from the request cookie, and replaces the ``csrf_token`` cookie (Req 4.9).
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    AuthenticateUseCase,
    SignInRequest,
    SignOutRequest,
    SignOutUseCase,
)
from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_application.resend_verification import (
    ResendVerificationRequest,
    ResendVerificationUseCase,
)
from edu_it_application.reset_password import (
    CompletePasswordResetRequest,
    CompletePasswordResetUseCase,
    RequestPasswordResetRequest,
    RequestPasswordResetUseCase,
    ResetCompletionOutcome,
)
from edu_it_application.sign_up import SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import (
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.ports.object_storage import ObjectStorage
from fastapi import APIRouter, Request, Response, status

from edu_it_api.accounts_schemas import (
    AcceptedResponse,
    AccountDeletionBody,
    CsrfResponse,
    ExportResponse,
    PasswordResetBody,
    PasswordResetCompleteBody,
    ResendVerificationBody,
    SessionResponse,
    SignInBody,
    SignInResponse,
    SignUpBody,
    VerifyEmailBody,
    VerifyEmailResponse,
)
from edu_it_api.csrf_tokens import ensure_csrf_cookie, new_csrf_token
from edu_it_api.errors.accounts import (
    DeletionNotConfirmed,
    ExportNotFound,
    InvalidResetLink,
    InvalidVerificationLink,
    ResetPasswordPolicyError,
)
from edu_it_api.errors.handlers import request_locale
from edu_it_api.identity import NotAuthenticated, SessionUserResolver
from edu_it_api.session_cookie import (
    CookieSettings,
    expire_session_cookie,
    set_csrf_cookie,
    set_session_cookie,
)

__all__ = [
    "DASHBOARD_PATH",
    "EXPORT_DOWNLOAD_PATH",
    "AccountServices",
    "build_account_router",
    "export_key",
]

EXPORT_DOWNLOAD_PATH = "/v1/account/export/download"
"""The path reported to the client for the export download (Req 13.1)."""


def export_key(user_stable_id: str) -> str:
    """The Object_Storage key of a user's export; derived from the Session, never a request."""
    return f"exports/{user_stable_id}.json"


DASHBOARD_PATH = "/dashboard"
"""Locale-free destination after sign-in; the Web_App prefixes the locale."""


@dataclass(frozen=True, slots=True)
class AccountServices:
    """The Milestone 1 use cases, resolver, and cookie settings the router needs."""

    resolver: SessionUserResolver
    cookies: CookieSettings
    sign_up: SignUpUseCase
    verify_email: VerifyEmailUseCase
    resend: ResendVerificationUseCase
    sign_in: AuthenticateUseCase
    sign_out: SignOutUseCase
    authenticate_session: AuthenticateSessionUseCase
    request_reset: RequestPasswordResetUseCase
    complete_reset: CompletePasswordResetUseCase
    export_data: ExportUserDataUseCase
    object_storage: ObjectStorage
    delete_account: DeleteAccountUseCase


def build_account_router(services: AccountServices) -> APIRouter:
    """Build the account router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["accounts"])

    @router.get("/auth/csrf", response_model=CsrfResponse)
    def csrf(request: Request, response: Response) -> CsrfResponse:
        """Issue (or echo) the CSRF_Token and its script-readable cookie."""
        return CsrfResponse(token=ensure_csrf_cookie(request, response, services.cookies))

    @router.post(
        "/auth/sign-up",
        response_model=AcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def sign_up(body: SignUpBody, request: Request) -> AcceptedResponse:
        """Create an unverified learner; the body is identical for known emails."""
        result = services.sign_up.execute(
            SignUpRequest(
                email=body.email,
                password=body.password,
                locale=request_locale(request),
            )
        )
        return AcceptedResponse(accepted=result.accepted)

    @router.post("/auth/verify-email", response_model=VerifyEmailResponse)
    def verify_email(body: VerifyEmailBody) -> VerifyEmailResponse:
        """Consume a verification token."""
        result = services.verify_email.execute(VerifyEmailRequest(token=body.token))
        if not result.is_verified:
            raise InvalidVerificationLink
        return VerifyEmailResponse()

    @router.post(
        "/auth/verify-email/resend",
        response_model=AcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def resend(body: ResendVerificationBody) -> AcceptedResponse:
        """Resend the verification mail; the body never discloses account state."""
        result = services.resend.execute(ResendVerificationRequest(email=body.email))
        return AcceptedResponse(accepted=result.accepted)

    @router.post("/auth/sign-in", response_model=SignInResponse)
    def sign_in(body: SignInBody, response: Response) -> SignInResponse:
        """Authenticate and rotate the Session_Cookie and the ``csrf_token`` cookie."""
        result = services.sign_in.execute(SignInRequest(email=body.email, password=body.password))
        set_session_cookie(response, result.server_side_ref, services.cookies)
        set_csrf_cookie(response, new_csrf_token(), services.cookies)
        return SignInResponse(redirect_to=DASHBOARD_PATH)

    @router.post(
        "/auth/password-reset",
        response_model=AcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def password_reset(body: PasswordResetBody) -> AcceptedResponse:
        """Request a reset mail; the body never discloses account state."""
        result = services.request_reset.execute(RequestPasswordResetRequest(email=body.email))
        return AcceptedResponse(accepted=result.accepted)

    @router.post("/auth/password-reset/complete", response_model=SignInResponse)
    def password_reset_complete(
        body: PasswordResetCompleteBody, response: Response
    ) -> SignInResponse:
        """Complete a reset; the use case revokes prior Sessions and creates one new one."""
        result = services.complete_reset.execute(
            CompletePasswordResetRequest(token=body.token, new_password=body.new_password)
        )
        if result.outcome is ResetCompletionOutcome.WEAK_PASSWORD:
            raise ResetPasswordPolicyError
        if not result.is_completed or result.server_side_ref is None:
            raise InvalidResetLink
        set_session_cookie(response, result.server_side_ref, services.cookies)
        set_csrf_cookie(response, new_csrf_token(), services.cookies)
        return SignInResponse(redirect_to=DASHBOARD_PATH)

    @router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT)
    def sign_out(request: Request) -> Response:
        """Revoke the presented Session, if any, and expire the cookie."""
        services.sign_out.execute(SignOutRequest(server_side_ref=services.resolver.ref(request)))
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        expire_session_cookie(response, services.cookies)
        return response

    @router.get("/auth/session", response_model=SessionResponse)
    def session(request: Request) -> SessionResponse:
        """Report the authenticated user, or ``401`` when no Session is active."""
        user = services.authenticate_session.authenticate(services.resolver.ref(request))
        if user is None:
            raise NotAuthenticated
        return SessionResponse(user_id=user.user_stable_id)

    @router.post("/account/export", response_model=ExportResponse)
    def export_account(request: Request) -> ExportResponse:
        """Write the caller's export to Object_Storage and say where to download it."""
        user_id = services.resolver(request)
        result = services.export_data.execute(ExportUserDataRequest(user_stable_id=user_id))
        return ExportResponse(download_path=EXPORT_DOWNLOAD_PATH, size_bytes=result.size_bytes)

    @router.get(
        "/account/export/download",
        response_class=Response,
        responses={200: {"content": {"application/json": {}}}},
    )
    def download_export(request: Request) -> Response:
        """Stream the caller's export; the key derives from the Session, never the request."""
        user_id = services.resolver(request)
        try:
            stored = services.object_storage.get(export_key(user_id))
        except KeyError:
            raise ExportNotFound from None
        return Response(
            content=stored.data,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="edu-it-export.json"'},
        )

    @router.post("/account/deletion", status_code=status.HTTP_204_NO_CONTENT)
    def delete_account(body: AccountDeletionBody, request: Request) -> Response:
        """Delete the caller's account after confirmation and expire the cookie."""
        user_id = services.resolver(request)
        if not body.confirm:
            raise DeletionNotConfirmed
        services.delete_account.execute(
            DeleteAccountRequest(user_stable_id=user_id, confirmed=True)
        )
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        expire_session_cookie(response, services.cookies)
        return response

    return router
