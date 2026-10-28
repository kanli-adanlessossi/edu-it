"""Tests for request-time datastore failure handling (task 20, Req 27.2).

When PostgreSQL or Redis becomes unavailable *during a request*, the API must
return a translated Problem_Details with a server-error status and record the
failure in the logs without exposing stack traces, connection strings, or host
identifiers in the response body (Req 27.2).

These tests drive the registered datastore handler directly (the FastAPI
``TestClient``/httpx is not a dependency of this layer), asserting:

- a real datastore outage becomes a ``503 Service Unavailable``
  ``application/problem+json`` document whose ``status`` member equals ``503``;
- the learner-facing ``title``/``detail`` follow the request locale with a
  default fallback;
- no stack trace, connection string, or host identifier reaches the body, while
  the full diagnostic is emitted to the logs;
- a non-outage error carried by the same base type still falls through to the
  generic ``500`` path;
- ``create_app`` wires a handler for each datastore exception family.

The broad no-leak guarantee across every error path (including datastore
outages) is also covered by Property 31 in
``test_errors_no_leak_properties.py``; this module adds the ``503``-specific
behavior and a focused property exercising the datastore handler across many
driver errors, locales, and paths.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from typing import Any

import redis.exceptions as redis_exceptions
from edu_it_api.app import create_app
from edu_it_api.errors import DEFAULT_LOCALE, PROBLEM_JSON_MEDIA_TYPE
from edu_it_api.errors.handlers import _handle_datastore_unavailable
from edu_it_infrastructure.datastore_errors import DATASTORE_UNAVAILABLE_ERRORS
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from starlette.requests import Request

# Sensitive fragments a datastore driver error typically carries: a connection
# string, internal host identifiers, SQL, and a stack-trace frame. None may
# reach the response body (Req 27.2).
_SENSITIVE_FRAGMENTS: tuple[str, ...] = (
    "postgresql://edu_it:s3cr3t@db.internal.edu-it.example:5432/edu_it",
    "db.internal.edu-it.example",
    "10.0.3.17:5432",
    "redis://cache.internal.edu-it.example:6379/0",
    "SELECT 1",
    'Traceback (most recent call last):\n  File "repositories.py", line 88',
)


def _request(path: str = "/v1/resource", locale: str | None = None) -> Request:
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


def _postgres_outage(message: str) -> OperationalError:
    """Build a PostgreSQL connection failure whose statement carries the secret."""
    return OperationalError(message, {}, Exception(message))


def _redis_outage(message: str) -> redis_exceptions.ConnectionError:
    return redis_exceptions.ConnectionError(message)


# --- 503 server-error status and problem shape (Req 27.2, 19.1, 19.2) --------


def test_postgres_outage_returns_503_problem_json() -> None:
    response = asyncio.run(
        _handle_datastore_unavailable(_request(), _postgres_outage("connection refused"))
    )
    assert response.status_code == int(HTTPStatus.SERVICE_UNAVAILABLE)
    assert response.media_type == PROBLEM_JSON_MEDIA_TYPE
    body = json.loads(bytes(response.body))
    assert body["status"] == int(HTTPStatus.SERVICE_UNAVAILABLE)
    assert {"type", "title"}.issubset(body)


def test_redis_outage_returns_503_problem_json() -> None:
    response = asyncio.run(
        _handle_datastore_unavailable(_request(), _redis_outage("Error 111 connecting"))
    )
    assert response.status_code == int(HTTPStatus.SERVICE_UNAVAILABLE)
    body = json.loads(bytes(response.body))
    assert body["status"] == int(HTTPStatus.SERVICE_UNAVAILABLE)


# --- Translated body with default fallback (Req 27.2, 19.3, 19.4) ------------


def test_detail_is_localized_per_request_locale() -> None:
    fr = asyncio.run(_handle_datastore_unavailable(_request(locale="fr"), _postgres_outage("down")))
    en = asyncio.run(_handle_datastore_unavailable(_request(locale="en"), _postgres_outage("down")))
    fr_body = json.loads(bytes(fr.body))
    en_body = json.loads(bytes(en.body))
    assert fr_body["detail"] and en_body["detail"]
    assert fr_body["detail"] != en_body["detail"]


def test_unsupported_locale_falls_back_to_default() -> None:
    de = asyncio.run(_handle_datastore_unavailable(_request(locale="de"), _postgres_outage("down")))
    default = asyncio.run(
        _handle_datastore_unavailable(_request(locale=DEFAULT_LOCALE), _postgres_outage("down"))
    )
    assert json.loads(bytes(de.body))["detail"] == json.loads(bytes(default.body))["detail"]


# --- No sensitive detail in the body; diagnostics logged (Req 27.2) ----------


def test_body_never_leaks_connection_string_or_host() -> None:
    for fragment in _SENSITIVE_FRAGMENTS:
        response = asyncio.run(
            _handle_datastore_unavailable(_request(), _postgres_outage(fragment))
        )
        body_text = bytes(response.body).decode("utf-8")
        for line in fragment.splitlines():
            token = line.strip()
            if token:
                assert token not in body_text, f"leaked into body: {token!r}"


def test_failure_is_logged_server_side(caplog: Any) -> None:
    secret = "postgresql://edu_it:s3cr3t@db.internal.edu-it.example:5432/edu_it"
    with caplog.at_level(logging.ERROR, logger="edu_it_api.errors"):
        asyncio.run(_handle_datastore_unavailable(_request(), _postgres_outage(secret)))
    # The failure is recorded server-side (the record exists); the body stays
    # sanitized regardless of what the log captures.
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


# --- Non-outage errors fall through to the generic 500 -----------------------


def test_non_outage_error_falls_through_to_500() -> None:
    bug = ProgrammingError("bad SQL", {}, Exception("syntax error"))
    response = asyncio.run(_handle_datastore_unavailable(_request(), bug))
    assert response.status_code == int(HTTPStatus.INTERNAL_SERVER_ERROR)
    body = json.loads(bytes(response.body))
    assert body["status"] == int(HTTPStatus.INTERNAL_SERVER_ERROR)


# --- App wiring --------------------------------------------------------------


def test_create_app_registers_a_handler_per_datastore_family() -> None:
    app = create_app()
    for datastore_error in DATASTORE_UNAVAILABLE_ERRORS:
        assert datastore_error in app.exception_handlers


# --- Property: datastore outages always yield a sanitized translated 503 -----

_outage_builders = st.sampled_from(
    [
        lambda m: OperationalError(m, {}, Exception(m)),
        lambda m: InterfaceError(m, {}, Exception(m)),
        lambda m: redis_exceptions.ConnectionError(m),
        lambda m: redis_exceptions.TimeoutError(m),
    ]
)
_locales = st.sampled_from(["fr", "en", "de", "es-ES", ""])
_paths = st.sampled_from(["/v1/resource", "/fr/dashboard", "/en/account", "/v1/users/42"])
_secret_messages = st.lists(
    st.sampled_from(_SENSITIVE_FRAGMENTS), min_size=1, max_size=len(_SENSITIVE_FRAGMENTS)
).map("\n".join)


@given(
    build_outage=_outage_builders,
    message=_secret_messages,
    locale=_locales,
    path=_paths,
)
@settings(max_examples=200)
def test_property_datastore_outage_returns_sanitized_translated_503(
    build_outage: Any,
    message: str,
    locale: str,
    path: str,
) -> None:
    """A request-time datastore outage always yields a sanitized, localized 503.

    For any PostgreSQL/Redis connection or operational failure — in any request
    locale and path, carrying a connection string, host identifier, SQL, and
    stack trace in its message — the handler returns a ``503``
    ``application/problem+json`` document whose ``status`` equals ``503`` and
    whose body leaks none of those diagnostics (Req 27.2).
    """
    request = _request(path=path, locale=locale or None)
    response = asyncio.run(_handle_datastore_unavailable(request, build_outage(message)))

    assert response.status_code == int(HTTPStatus.SERVICE_UNAVAILABLE)
    assert response.media_type == PROBLEM_JSON_MEDIA_TYPE

    body_text = bytes(response.body).decode("utf-8")
    body = json.loads(body_text)
    assert body["status"] == int(HTTPStatus.SERVICE_UNAVAILABLE)
    assert {"type", "title"}.issubset(body)
    assert body.get("detail")

    for fragment in _SENSITIVE_FRAGMENTS:
        for line in fragment.splitlines():
            token = line.strip()
            if token:
                assert token not in body_text
