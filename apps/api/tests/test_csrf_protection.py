"""Unit tests for CSRF protection on unsafe mutations (task 17.1, Req 11).

These cover the gating behavior without a running server: the method
classifier (Req 11.3, 11.4), the double-submit token verifier (Req 11.1, 11.2),
and the middleware's accept/reject decisions (Req 11.1-11.4). The middleware is
driven by constructing a Starlette ``Request`` and invoking ``dispatch`` with a
sentinel ``call_next``, the same server-less technique the task-8 error handlers
and task-9 health routes use, since the FastAPI ``TestClient`` (httpx) is not a
dependency of this layer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from edu_it_api.app import create_app
from edu_it_api.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SAFE_METHODS,
    CsrfProtectionMiddleware,
    is_safe_method,
    token_is_valid,
)
from edu_it_api.errors import PROBLEM_JSON_MEDIA_TYPE
from edu_it_api.errors.catalog import CSRF
from edu_it_api.middleware import RequestLoggingMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_VALID_TOKEN = "abcdef0123456789abcdef0123456789"
_UNSAFE_METHODS = ["POST", "PUT", "PATCH", "DELETE"]
_SENTINEL_STATUS = 204


def _request(
    *,
    method: str,
    path: str = "/v1/resource",
    header_token: str | None = None,
    cookie_token: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if header_token is not None:
        headers.append((CSRF_HEADER_NAME.encode("latin-1"), header_token.encode("latin-1")))
    if cookie_token is not None:
        headers.append((b"cookie", f"{CSRF_COOKIE_NAME}={cookie_token}".encode("latin-1")))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)


async def _passthrough(_request: Request) -> Response:
    """A ``call_next`` that proves the request reached the route (was not gated)."""
    return JSONResponse({"ok": True}, status_code=_SENTINEL_STATUS)


async def _unused_asgi_app(scope: Any, receive: Any, send: Any) -> None:
    """A never-called ASGI app satisfying the middleware constructor's type."""
    raise AssertionError("the wrapped ASGI app is bypassed by the sentinel call_next")


def _dispatch(request: Request) -> Response:
    middleware = CsrfProtectionMiddleware(app=_unused_asgi_app)
    return asyncio.run(middleware.dispatch(request, _passthrough))


def _body(response: Response) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(bytes(response.body))
    return parsed


# --- Method classification (Req 11.3, 11.4) ----------------------------------


def test_safe_methods_need_no_token() -> None:
    for method in SAFE_METHODS:
        assert is_safe_method(method)


def test_classifier_is_case_insensitive() -> None:
    assert is_safe_method("get")
    assert is_safe_method("Get")
    assert not is_safe_method("post")


def test_unsafe_methods_are_not_safe() -> None:
    for method in _UNSAFE_METHODS:
        assert not is_safe_method(method)


# --- Token verifier (Req 11.1, 11.2) -----------------------------------------


def test_matching_well_formed_pair_is_valid() -> None:
    assert token_is_valid(_VALID_TOKEN, _VALID_TOKEN)


def test_mismatched_pair_is_invalid() -> None:
    assert not token_is_valid(_VALID_TOKEN, _VALID_TOKEN[::-1])


def test_missing_header_or_cookie_is_invalid() -> None:
    assert not token_is_valid(None, _VALID_TOKEN)
    assert not token_is_valid(_VALID_TOKEN, None)
    assert not token_is_valid(None, None)


def test_malformed_short_or_empty_token_is_invalid() -> None:
    assert not token_is_valid("short", "short")
    assert not token_is_valid("", "")
    assert not token_is_valid("   ", "   ")


# --- Middleware: safe methods pass without a token (Req 11.3) -----------------


def test_safe_methods_pass_through_with_no_token() -> None:
    for method in SAFE_METHODS:
        response = _dispatch(_request(method=method))
        assert response.status_code == _SENTINEL_STATUS


# --- Middleware: unsafe methods require a valid token (Req 11.1, 11.4) --------


def test_unsafe_method_with_valid_token_is_processed() -> None:
    for method in _UNSAFE_METHODS:
        response = _dispatch(
            _request(method=method, header_token=_VALID_TOKEN, cookie_token=_VALID_TOKEN)
        )
        assert response.status_code == _SENTINEL_STATUS


# --- Middleware: rejection with Problem_Details and no state change (Req 11.2) -


def test_unsafe_method_missing_token_is_rejected() -> None:
    response = _dispatch(_request(method="POST"))
    assert response.status_code == CSRF.status
    assert response.status_code == 403
    assert response.media_type == PROBLEM_JSON_MEDIA_TYPE


def test_unsafe_method_only_cookie_is_rejected() -> None:
    response = _dispatch(_request(method="POST", cookie_token=_VALID_TOKEN))
    assert response.status_code == 403


def test_unsafe_method_only_header_is_rejected() -> None:
    response = _dispatch(_request(method="POST", header_token=_VALID_TOKEN))
    assert response.status_code == 403


def test_unsafe_method_mismatched_token_is_rejected() -> None:
    response = _dispatch(
        _request(method="POST", header_token=_VALID_TOKEN, cookie_token=_VALID_TOKEN[::-1])
    )
    assert response.status_code == 403


def test_unsafe_method_malformed_token_is_rejected() -> None:
    response = _dispatch(_request(method="POST", header_token="x", cookie_token="x"))
    assert response.status_code == 403


def test_rejection_body_is_a_localized_problem_document() -> None:
    response = _dispatch(_request(method="POST", path="/fr/sign-up"))
    body = _body(response)
    assert body["status"] == 403
    assert {"type", "title", "detail"}.issubset(body)
    # Localized to the request's /fr route segment.
    assert body["detail"] == CSRF.detail_for("fr")
    assert body["instance"] == "/fr/sign-up"


def test_rejection_body_carries_no_internal_diagnostic() -> None:
    response = _dispatch(_request(method="POST", header_token="x", cookie_token="y"))
    text = bytes(response.body).decode("utf-8")
    # The body must not reveal which half of the pair failed.
    assert "cookie" not in text.lower()
    assert "header" not in text.lower()


# --- App wiring (Req 11) ------------------------------------------------------


def test_create_app_registers_the_csrf_middleware_inside_request_logging() -> None:
    app = create_app()
    names = [getattr(m.cls, "__name__", "") for m in app.user_middleware]
    assert CsrfProtectionMiddleware.__name__ in names
    assert RequestLoggingMiddleware.__name__ in names
    # Starlette runs the most-recently-added middleware outermost. The logger is
    # added last, so it must appear before the CSRF guard, making logging the
    # outer layer that records even a CSRF-rejected request.
    assert names.index(RequestLoggingMiddleware.__name__) < names.index(
        CsrfProtectionMiddleware.__name__
    )
