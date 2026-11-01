"""GitHub OAuth routes with PKCE under ``/v1/auth/github`` (api-route-wiring Req 6).

``new_pkce_verifier`` makes the random ``code_verifier`` kept in the state cookie;
``pkce_challenge`` derives the S256 ``code_challenge`` sent to GitHub. The router is a
thin boundary over the existing OAuth use cases: ``authorize`` stores ``state`` and the
verifier in the ``edu_it_oauth_state`` cookie and redirects to GitHub; ``callback``
checks ``state`` in constant time, exchanges the code, calls
``CompleteOAuthCallbackUseCase``, deletes the state cookie in every outcome, and
redirects to the Web_App; ``confirm`` and ``decline`` finish a pending link.

Both OAuth cookies are host-only (no ``Domain``) with ``Path=/v1/auth/github``, because
only the API host reads them. The Session_Cookie and ``csrf_token`` cookie are set only
after a use case returns a new Session, from the reference the use case created.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote, urlencode

from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    ConfirmOAuthLinkRequest,
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkRequest,
    DeclineOAuthLinkUseCase,
    OAuthCallback,
    OAuthCallbackError,
    OAuthCallbackFailure,
    OAuthCallbackStatus,
)
from edu_it_domain.config import OAUTH_LINK_CONFIRMATION_WINDOW, OAUTH_STATE_TTL
from edu_it_domain.ports.github_oauth import GitHubOAuthClient
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from edu_it_api.accounts import DASHBOARD_PATH
from edu_it_api.accounts_schemas import SignInResponse
from edu_it_api.csrf_tokens import new_csrf_token
from edu_it_api.errors.accounts import InvalidOAuthLink
from edu_it_api.errors.handlers import request_locale
from edu_it_api.session_cookie import CookieSettings, set_csrf_cookie, set_session_cookie

__all__ = [
    "OAUTH_PENDING_COOKIE_NAME",
    "OAUTH_STATE_COOKIE_NAME",
    "OAuthServices",
    "build_oauth_router",
    "new_pkce_verifier",
    "pkce_challenge",
]

OAUTH_STATE_COOKIE_NAME: Final = "edu_it_oauth_state"
"""Cookie holding ``{state}.{code_verifier}`` between authorize and callback."""

OAUTH_PENDING_COOKIE_NAME: Final = "edu_it_oauth_pending"
"""Temporary unauthenticated cookie holding the pending link token (Req 6.4)."""

_COOKIE_PATH: Final = "/v1/auth/github"
_SAME_SITE: Final = "lax"

_ERROR_CODES: Final[dict[OAuthCallbackFailure, str]] = {
    OAuthCallbackFailure.REJECTED: "oauth_rejected",
    OAuthCallbackFailure.EMAIL_UNVERIFIED: "oauth_email_unverified",
}


def new_pkce_verifier() -> str:
    """Return a random ``code_verifier``: 86 URL-safe characters, no dot."""
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """Return the unpadded base64url SHA-256 of ``verifier`` (43 characters)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class OAuthServices:
    """The OAuth client, use cases, and settings the OAuth router needs."""

    client: GitHubOAuthClient
    cookies: CookieSettings
    web_base_url: str
    complete: CompleteOAuthCallbackUseCase
    confirm: ConfirmOAuthLinkUseCase
    decline: DeclineOAuthLinkUseCase


def _set_oauth_cookie(
    response: Response, name: str, value: str, max_seconds: int, services: OAuthServices
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_seconds,
        path=_COOKIE_PATH,
        secure=services.cookies.secure,
        httponly=True,
        samesite=_SAME_SITE,
    )


def _delete_oauth_cookie(response: Response, name: str, services: OAuthServices) -> None:
    response.delete_cookie(
        key=name,
        path=_COOKIE_PATH,
        secure=services.cookies.secure,
        httponly=True,
        samesite=_SAME_SITE,
    )


def _split_state_cookie(value: str | None) -> tuple[str, str] | None:
    """Return ``(state, verifier)`` from the cookie value, or ``None`` when malformed."""
    if not value:
        return None
    state, dot, verifier = value.partition(".")
    if not dot or not state or not verifier:
        return None
    return state, verifier


def _web_url(services: OAuthServices, request: Request, path: str, query: str = "") -> str:
    base = services.web_base_url.rstrip("/")
    locale = quote(request_locale(request), safe="")
    return f"{base}/{locale}{path}{query}"


def build_oauth_router(services: OAuthServices) -> APIRouter:
    """Build the GitHub OAuth router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["oauth"])

    @router.get("/auth/github/authorize", status_code=status.HTTP_302_FOUND)
    def authorize() -> Response:
        """Start the flow: store ``state`` and the PKCE verifier, redirect to GitHub."""
        state = secrets.token_urlsafe(32)
        verifier = new_pkce_verifier()
        url = services.client.authorize_url(state, pkce_challenge(verifier))
        response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
        _set_oauth_cookie(
            response,
            OAUTH_STATE_COOKIE_NAME,
            f"{state}.{verifier}",
            int(OAUTH_STATE_TTL.total_seconds()),
            services,
        )
        return response

    @router.get("/auth/github/callback", status_code=status.HTTP_302_FOUND)
    def callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> Response:
        """Finish the flow and redirect to the Web_App; the state cookie never survives."""
        stored = _split_state_cookie(request.cookies.get(OAUTH_STATE_COOKIE_NAME))
        state_matches = (
            stored is not None
            and state is not None
            and hmac.compare_digest(stored[0].encode(), state.encode())
        )
        identity = None
        if stored is not None and state_matches and code and error is None:
            identity = services.client.exchange(code, stored[1])
        callback_value = OAuthCallback(
            completed=identity is not None,
            state_matches=state_matches,
            subject=identity.subject if identity else "",
            email=identity.email if identity else "",
            email_verified=identity.email_verified if identity else False,
        )
        response: Response
        try:
            result = services.complete.execute(callback_value)
        except OAuthCallbackError as exc:
            query = urlencode({"error": _ERROR_CODES[exc.failure]})
            response = RedirectResponse(
                _web_url(services, request, "/sign-in", f"?{query}"),
                status_code=status.HTTP_302_FOUND,
            )
        else:
            if result.status is OAuthCallbackStatus.SIGNED_IN and result.server_side_ref:
                response = RedirectResponse(
                    _web_url(services, request, DASHBOARD_PATH),
                    status_code=status.HTTP_302_FOUND,
                )
                set_session_cookie(response, result.server_side_ref, services.cookies)
                set_csrf_cookie(response, new_csrf_token(), services.cookies)
            else:
                response = RedirectResponse(
                    _web_url(services, request, "/auth/github/confirm"),
                    status_code=status.HTTP_302_FOUND,
                )
                # Only the unauthenticated pending cookie: no Session_Cookie (Req 6.4).
                assert result.pending_token is not None
                _set_oauth_cookie(
                    response,
                    OAUTH_PENDING_COOKIE_NAME,
                    result.pending_token,
                    int(OAUTH_LINK_CONFIRMATION_WINDOW.total_seconds()),
                    services,
                )
        _delete_oauth_cookie(response, OAUTH_STATE_COOKIE_NAME, services)
        return response

    @router.post("/auth/github/confirm", response_model=SignInResponse)
    def confirm(request: Request, response: Response) -> SignInResponse:
        """Confirm the pending link: new Session, fresh ``csrf_token``, pending cookie gone."""
        token = request.cookies.get(OAUTH_PENDING_COOKIE_NAME)
        result = services.confirm.execute(ConfirmOAuthLinkRequest(token=token or ""))
        if not result.is_linked or result.server_side_ref is None:
            raise InvalidOAuthLink
        set_session_cookie(response, result.server_side_ref, services.cookies)
        set_csrf_cookie(response, new_csrf_token(), services.cookies)
        _delete_oauth_cookie(response, OAUTH_PENDING_COOKIE_NAME, services)
        return SignInResponse(redirect_to=DASHBOARD_PATH)

    @router.post("/auth/github/decline", status_code=status.HTTP_204_NO_CONTENT)
    def decline(request: Request) -> Response:
        """Invalidate the pending link at once and expire the pending cookie."""
        token = request.cookies.get(OAUTH_PENDING_COOKIE_NAME)
        if token:
            services.decline.execute(DeclineOAuthLinkRequest(token=token))
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        _delete_oauth_cookie(response, OAUTH_PENDING_COOKIE_NAME, services)
        return response

    return router
