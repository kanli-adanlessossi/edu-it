"""Property-based test for CSRF gating of unsafe mutations (task 17.3).

Feature: foundations, Property 16: CSRF protection gates exactly the unsafe mutations

Property 16 (design.md): *For any* request, it is processed if and only if its
method is safe (and then no CSRF_Token is required) or its method is unsafe and
it carries a valid CSRF_Token; an unsafe request with a missing, malformed, or
invalid token is rejected with no state change and a Problem_Details response.

The CSRF guard lives at the ``apps/api`` boundary as a Starlette middleware
(``CsrfProtectionMiddleware``) split over the pure classifier ``is_safe_method``
and verifier ``token_is_valid``. This property drives the middleware over
arbitrary methods and arbitrary double-submit header/cookie token pairs --
present, absent, malformed, matching, and mismatched -- and checks the single
"exactly" biconditional: the request reaches the route (a sentinel ``call_next``
that stands in for the mutation) *if and only if* it is allowed, and is
otherwise rejected with a ``403`` ``application/problem+json`` document while the
mutation never runs (no state change).

The middleware is driven by constructing a Starlette ``Request`` and awaiting
``dispatch`` with a sentinel ``call_next``, the same server-less technique the
task-8 error handlers, the task-9 health routes, and the task-17.1 unit tests
use, so the property needs no running server or HTTP client dependency. Specific
examples and edge cases are covered by the unit tests in
``test_csrf_protection.py``.

Validates: Requirements 11.1, 11.2, 11.3, 11.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import asyncio
from typing import Any

from edu_it_api.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SAFE_METHODS,
    CsrfProtectionMiddleware,
    is_safe_method,
    token_is_valid,
)
from edu_it_api.errors import PROBLEM_JSON_MEDIA_TYPE
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# A sentinel status the stand-in route returns. It differs from the guard's
# rejection status (403) so "the request reached the route" and "the request was
# rejected" are unambiguous from the response code alone.
_ROUTE_REACHED_STATUS = 204
_REJECTED_STATUS = 403

# The HTTP methods a client can issue: the safe/idempotent ones that need no
# token plus the unsafe mutating ones the guard gates. Mixed case exercises the
# classifier's case-insensitivity.
_METHODS = st.sampled_from(
    [*sorted(SAFE_METHODS), "POST", "PUT", "PATCH", "DELETE", "get", "Post", "Delete"]
)

# A well-formed, high-entropy token like the web app issues (>= the guard's
# minimum length). Reused on both sides to build a *matching* pair.
_well_formed_token = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=16, max_size=48
)

# An arbitrary token *slot* for either the header or the cookie: absent, blank,
# too-short (malformed), or a well-formed value. Drawing each side independently
# spans the missing / malformed / mismatched / matching cases.
_token_slot = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.text(alphabet="abc123", max_size=8),  # usually too short -> malformed
    _well_formed_token,
)


async def _route_reached(_request: Request) -> Response:
    """A ``call_next`` standing in for the mutation; reaching it means not gated."""
    return JSONResponse({"ok": True}, status_code=_ROUTE_REACHED_STATUS)


async def _unused_asgi_app(scope: Any, receive: Any, send: Any) -> None:
    """A never-called ASGI app satisfying the middleware constructor's type."""
    raise AssertionError("the wrapped ASGI app is bypassed by the sentinel call_next")


def _request(method: str, header_token: str | None, cookie_token: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if header_token is not None:
        headers.append((CSRF_HEADER_NAME.encode("latin-1"), header_token.encode("latin-1")))
    if cookie_token is not None:
        headers.append((b"cookie", f"{CSRF_COOKIE_NAME}={cookie_token}".encode("latin-1")))
    scope = {
        "type": "http",
        "method": method,
        "path": "/v1/resource",
        "raw_path": b"/v1/resource",
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)


def _dispatch(request: Request) -> Response:
    middleware = CsrfProtectionMiddleware(app=_unused_asgi_app)
    return asyncio.run(middleware.dispatch(request, _route_reached))


@given(method=_METHODS, header_token=_token_slot, cookie_token=_token_slot)
@settings(max_examples=200)
def test_property_16_csrf_gates_exactly_the_unsafe_mutations(
    method: str,
    header_token: str | None,
    cookie_token: str | None,
) -> None:
    """Feature: foundations, Property 16: CSRF protection gates exactly the unsafe mutations.

    For any request, it is processed if and only if its method is safe (then no
    token is required) or its method is unsafe and it carries a valid
    double-submit CSRF_Token; otherwise it is rejected with a 403
    Problem_Details response and the mutation never runs.
    """
    # The decision the gate *should* make, derived independently from the two
    # pure predicates: a request is allowed exactly when the method is safe
    # (Req 11.3) or it is unsafe but carries a valid token pair (Req 11.1, 11.4).
    should_be_allowed = is_safe_method(method) or token_is_valid(header_token, cookie_token)

    response = _dispatch(_request(method, header_token, cookie_token))

    if should_be_allowed:
        # The request reached the route: the mutation would run (Req 11.1, 11.3).
        assert response.status_code == _ROUTE_REACHED_STATUS
    else:
        # The request was rejected before the route, so no state change, and the
        # rejection is a problem document carrying the 403 status (Req 11.2).
        assert response.status_code == _REJECTED_STATUS
        content_type = response.headers.get("content-type", "")
        assert content_type.split(";", 1)[0] == PROBLEM_JSON_MEDIA_TYPE

    # Biconditional, stated the other way: reaching the route happens exactly
    # when the request should be allowed -- the gate is neither stricter nor
    # looser than the safe-or-valid-token rule.
    assert (response.status_code == _ROUTE_REACHED_STATUS) == should_be_allowed
