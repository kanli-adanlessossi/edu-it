"""CSRF_Token issuance for the double-submit scheme (api-route-wiring Req 2.4-2.6).

:mod:`edu_it_api.csrf` verifies the pair; this module mints the token and sets the
cookie. A credential-establishing route always calls :func:`new_csrf_token` so the
token rotates with the Session.
"""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import Response

from edu_it_api.csrf import CSRF_COOKIE_NAME, token_is_valid
from edu_it_api.session_cookie import CookieSettings, set_csrf_cookie

__all__ = ["ensure_csrf_cookie", "new_csrf_token"]


def new_csrf_token() -> str:
    """Return a fresh high-entropy URL-safe CSRF_Token (32 random bytes)."""
    return secrets.token_urlsafe(32)


def ensure_csrf_cookie(request: Request, response: Response, settings: CookieSettings) -> str:
    """Return the request's well-formed ``csrf_token``, or issue a new one on ``response``."""
    existing = request.cookies.get(CSRF_COOKIE_NAME)
    if existing is not None and token_is_valid(existing, existing):
        return existing
    token = new_csrf_token()
    set_csrf_cookie(response, token, settings)
    return token
