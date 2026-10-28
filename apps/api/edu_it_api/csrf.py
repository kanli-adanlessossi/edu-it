"""CSRF protection for unsafe mutations at the ``apps/api`` boundary (Req 11).

The account system mutates server-side state (sign-up, sign-in/out, password
reset, OAuth linking) through unsafe HTTP methods. A forged cross-site request
could ride a learner's ambient session cookie and act on their behalf, so every
unsafe mutation must carry a CSRF_Token the attacker cannot forge (Req 11).

This module is the single enforcement point. It is split into *pure* classifier
and verifier functions and a thin Starlette middleware so the gating logic can be
exercised by the property test (task 17.3) without a running server, matching the
task-8 error handlers and task-9 health routes.

Mechanism (double-submit cookie)
--------------------------------
The web app holds the CSRF_Token in a readable cookie (``csrf_token``) and echoes
it on every unsafe request in the ``X-CSRF-Token`` header. The boundary accepts
the request only when both are present, well-formed, and equal under a
constant-time comparison. A same-origin page can read the cookie and set the
header; a cross-site attacker can cause the cookie to be *sent* but cannot read
it to populate the header, so a forged request never carries a matching pair.
No shared secret or server round-trip is needed, which keeps the boundary thin.

Method classification (``docs/API-CONVENTIONS.md`` Section 5)
-------------------------------------------------------------
D2 Section 5 classifies the mutating methods — the "unsafe, non-idempotent
mutations (notably ``POST``)" — as the ones a CSRF_Token guards, and names the
safe, already-idempotent methods (``GET``) as the ones that need no token
(Req 11.3, 11.4). The read-only methods ``HEAD``, ``OPTIONS``, and ``TRACE`` are
safe alongside ``GET`` (they cannot change state and browsers issue ``OPTIONS``
as unauthenticated CORS preflights), so they require no token; every other method
(``POST``, ``PUT``, ``PATCH``, ``DELETE``) is treated as unsafe and gated.

Rejection (Req 11.2)
--------------------
The middleware runs *before* any route handler, so a missing, malformed, or
invalid token is rejected with a ``403`` ``application/problem+json`` document and
the request never reaches the mutation — guaranteeing no state change. Internal
diagnostics (which half of the pair was wrong) never reach the body.
"""

from __future__ import annotations

import hmac
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from edu_it_api.errors.catalog import CSRF
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.problem import build_problem

__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "SAFE_METHODS",
    "CsrfProtectionMiddleware",
    "is_safe_method",
    "token_is_valid",
]

#: HTTP methods that are safe and idempotent per ``docs/API-CONVENTIONS.md``
#: Section 5 and therefore require no CSRF_Token (Req 11.3). ``GET`` is named in
#: D2; ``HEAD``/``OPTIONS``/``TRACE`` are read-only alongside it (and ``OPTIONS``
#: carries CORS preflights that must pass unauthenticated).
SAFE_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: The cookie the web app stores the readable CSRF_Token in (double-submit).
CSRF_COOKIE_NAME: Final[str] = "csrf_token"

#: The request header the web app echoes the CSRF_Token in on unsafe mutations.
CSRF_HEADER_NAME: Final[str] = "x-csrf-token"

#: Minimum accepted token length. A token shorter than this carries too little
#: entropy to resist guessing and is treated as malformed (Req 11.2). The web
#: app issues a high-entropy token (e.g. a 32-byte URL-safe string); this bound
#: only rejects obviously malformed values, never a well-formed one.
_MIN_TOKEN_LENGTH: Final[int] = 16


def is_safe_method(method: str) -> bool:
    """Return whether ``method`` is a safe, idempotent method needing no token.

    The comparison is case-insensitive so a lowercase or mixed-case method name
    (which the ASGI layer may pass through) is classified the same as its
    canonical uppercase form. Every method not in :data:`SAFE_METHODS` is unsafe
    and must carry a valid CSRF_Token (Req 11.4).
    """
    return method.upper() in SAFE_METHODS


def _is_well_formed(token: str | None) -> bool:
    """Return whether ``token`` is present and not obviously malformed.

    A token is malformed when it is absent, empty/whitespace, or shorter than the
    minimum length; such values are rejected as invalid (Req 11.2).
    """
    if token is None:
        return False
    stripped = token.strip()
    return len(stripped) >= _MIN_TOKEN_LENGTH


def token_is_valid(header_token: str | None, cookie_token: str | None) -> bool:
    """Return whether the double-submit CSRF_Token pair is valid (Req 11.1, 11.2).

    The pair is valid only when both the header and cookie tokens are present and
    well-formed and are *equal*. The comparison uses a constant-time check
    (:func:`hmac.compare_digest`) so a near-miss token cannot be discovered by
    timing. A missing, malformed, or mismatched pair is invalid, and the caller
    rejects the request without performing the mutation (Req 11.2).
    """
    if not _is_well_formed(header_token) or not _is_well_formed(cookie_token):
        return False
    assert header_token is not None and cookie_token is not None  # narrowed by guards above
    return hmac.compare_digest(header_token.strip(), cookie_token.strip())


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    """Reject unsafe mutations lacking a valid CSRF_Token before they run (Req 11).

    For a safe, idempotent method the request passes straight through with no
    token required (Req 11.3). For an unsafe method the double-submit pair (the
    ``X-CSRF-Token`` header and the ``csrf_token`` cookie) must be present,
    well-formed, and equal (Req 11.1, 11.4); otherwise the request is rejected
    here — before any route handler executes, so no state changes — with a
    localized ``403`` Problem_Details document (Req 11.2).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if is_safe_method(request.method):
            return await call_next(request)

        header_token = request.headers.get(CSRF_HEADER_NAME)
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        if token_is_valid(header_token, cookie_token):
            return await call_next(request)

        return self._rejection(request)

    @staticmethod
    def _rejection(request: Request) -> Response:
        """Build the localized ``403`` Problem_Details for a failed check (Req 11.2).

        The request is rejected without being forwarded, so no mutation runs and
        no state changes. The body names no diagnostic about which half of the
        double-submit pair was missing or wrong.
        """
        locale = request_locale(request)
        problem = build_problem(
            status=CSRF.status,
            locale=locale,
            problem_type=CSRF,
            instance=request.url.path,
        )
        return problem_response(problem)
