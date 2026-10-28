"""Property-based test for no internal-diagnostic leakage (task 8.6).

Feature: foundations, Property 31: Error responses never leak internal diagnostics

Property 31 (design.md): *For any* request that produces an error response —
including when PostgreSQL or Redis is unavailable during the request — no member
of the response body contains a stack trace, SQL, a provider error, a connection
string, a host identifier, or any internal representation; such diagnostics
appear only in the logs.

This single property drives every error-response path the boundary produces — a
status-typed ``HTTPException`` problem, a Pydantic v2 validation problem, and the
generic ``500`` from an unhandled exception (standing in for a request-time
PostgreSQL/Redis outage, a leaked SQL string, a provider error, a connection
string, and a host identifier) — and asserts that the exact diagnostic material
fed to the handler never reaches the serialized ``application/problem+json``
body. The matching example/edge-case unit tests live in
``test_errors_problem_details.py``.

Validates: Requirements 18.6, 19.6, 27.2

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any

from edu_it_api.errors.handlers import (
    _handle_http_exception,
    _handle_unexpected_error,
    _handle_validation_error,
)
from fastapi.exceptions import RequestValidationError
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

# Concrete internal-diagnostic fragments the handler must never echo into a
# response body: a stack-trace frame, SQL text, a provider error, a datastore
# connection string, and a host identifier (Req 18.6, 19.6, 27.2). Each is a
# distinctive token so a substring check reliably detects any leak.
_DIAGNOSTIC_FRAGMENTS: tuple[str, ...] = (
    'Traceback (most recent call last):\n  File "db.py", line 42, in query',
    "SELECT * FROM users WHERE email = 'admin@example.com'",
    "psycopg.OperationalError: connection to server failed",
    "redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379",
    "postgresql://edu_it:s3cr3t@db.internal.edu-it.example:5432/edu_it",
    "db.internal.edu-it.example",
    "10.0.3.17:5432",
)

# Datastore-failure exception types standing in for a request-time PostgreSQL or
# Redis outage (Req 27.2): any unhandled error reaches the generic-500 handler.
_DATASTORE_ERRORS: tuple[type[Exception], ...] = (RuntimeError, ConnectionError, OSError)

# Supported request locales plus unsupported/absent ones that fall back to the
# default; the leak guarantee must hold in every locale.
_locales = st.sampled_from(["fr", "en", "de", "es-ES", ""])

# A request path; the handler echoes it as ``instance``, so it must not itself
# carry a diagnostic (these are ordinary route shapes).
_paths = st.sampled_from(["/v1/resource", "/fr/dashboard", "/en/account", "/v1/users/42"])

# Non-2xx statuses the HTTPException handler renders.
_error_statuses = st.sampled_from([400, 401, 403, 404, 405, 409, 422, 429, 500, 503, 418])

# One or more raw diagnostic fragments composed into a single exception message,
# mirroring how a real stack/SQL/provider error arrives concatenated.
_diagnostic_messages = st.lists(
    st.sampled_from(_DIAGNOSTIC_FRAGMENTS), min_size=1, max_size=len(_DIAGNOSTIC_FRAGMENTS)
).map("\n".join)


def _request(path: str, locale: str) -> Request:
    """Build a minimal Starlette request carrying ``locale`` via Accept-Language."""
    headers = [(b"accept-language", locale.encode("latin-1"))] if locale else []
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)


def _body_strings(value: Any) -> list[str]:
    """Collect every string that appears anywhere in a problem-body structure.

    Walks the decoded JSON (dicts, lists, and scalars) so the leak check inspects
    every member and nested extension value, not just the top-level ``detail``.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, item in value.items():
            strings.append(str(key))
            strings.extend(_body_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_body_strings(item))
        return strings
    return []


def _assert_no_leak(raw_body: bytes, diagnostics: list[str]) -> None:
    """Assert no diagnostic fragment appears anywhere in the serialized body."""
    body = json.loads(raw_body.decode("utf-8"))
    haystack = "\n".join(_body_strings(body))
    for fragment in diagnostics:
        for line in fragment.splitlines():
            token = line.strip()
            if token:
                assert token not in haystack, (
                    f"internal diagnostic leaked into problem body: {token!r}"
                )
    # The raw serialized bytes must also be free of the fragments, catching any
    # leak that a structure walk might miss (e.g. an unexpected member).
    raw_text = raw_body.decode("utf-8")
    for fragment in diagnostics:
        for line in fragment.splitlines():
            token = line.strip()
            if token:
                assert token not in raw_text


class _Payload(BaseModel):
    email: str
    password: str
    age: int


def _validation_errors(rejected: str) -> list[dict[str, Any]]:
    """Produce Pydantic v2 errors whose rejected input is a diagnostic string.

    Feeds the diagnostic fragment in as the rejected value so the property also
    proves the rejected input itself is never echoed back (Req 19.6).
    """
    try:
        _Payload.model_validate({"email": rejected, "age": rejected})
    except ValidationError as exc:
        return [dict(error) for error in exc.errors()]
    raise AssertionError("expected a ValidationError")


@given(
    message=_diagnostic_messages,
    error_type=st.sampled_from(_DATASTORE_ERRORS),
    status=_error_statuses,
    locale=_locales,
    path=_paths,
    rejected=st.sampled_from(_DIAGNOSTIC_FRAGMENTS),
)
@settings(max_examples=200)
def test_property_31_error_responses_never_leak_internal_diagnostics(
    message: str,
    error_type: type[Exception],
    status: int,
    locale: str,
    path: str,
    rejected: str,
) -> None:
    """Feature: foundations, Property 31: Error responses never leak internal diagnostics.

    Across the generic-500 path (an unhandled datastore/provider error carrying a
    stack trace, SQL, a connection string, and a host identifier), the
    status-typed ``HTTPException`` path, and the Pydantic v2 validation path
    (whose rejected input is itself a diagnostic string), no member of the
    serialized ``application/problem+json`` body contains any of those internal
    diagnostics (Req 18.6, 19.6, 27.2).
    """
    request = _request(path, locale)

    # Generic 500: an unhandled exception standing in for a request-time
    # PostgreSQL/Redis outage or provider error whose message bundles a stack
    # trace, SQL, a connection string, and a host identifier (Req 27.2).
    unexpected = asyncio.run(_handle_unexpected_error(request, error_type(message)))
    assert unexpected.status_code == int(HTTPStatus.INTERNAL_SERVER_ERROR)
    _assert_no_leak(bytes(unexpected.body), [message])

    # Status-typed HTTPException: the raised detail may carry diagnostics too.
    http_exc = StarletteHTTPException(status_code=status, detail=message)
    http_response = asyncio.run(_handle_http_exception(request, http_exc))
    assert http_response.status_code == status
    _assert_no_leak(bytes(http_response.body), [message])

    # Validation failure: the rejected input value is a diagnostic fragment and
    # must never be echoed back in the ``fields`` extension or anywhere else.
    validation_exc = RequestValidationError(_validation_errors(rejected))
    validation_response = asyncio.run(_handle_validation_error(request, validation_exc))
    assert validation_response.status_code == int(HTTPStatus.UNPROCESSABLE_ENTITY)
    _assert_no_leak(bytes(validation_response.body), [rejected])
