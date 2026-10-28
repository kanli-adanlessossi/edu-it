"""Property-based test for non-2xx problem documents (task 8.3).

Feature: foundations, Property 28: Every non-2xx response is a problem document with a consistent status

Property 28 (design.md): *For any* request that yields a non-2xx response, the
response is served as ``application/problem+json`` carrying at least ``type``,
``title``, and ``status``, and the body's ``status`` member equals the
response's HTTP status code.

A non-2xx response can arise three ways at the boundary: a route raises an
``HTTPException`` with an arbitrary non-2xx status, a Pydantic v2 body fails
validation (422), or a route raises an unhandled exception (500). This drives
each cause through the *actual* handlers ``create_app`` registers — resolved
from ``app.exception_handlers`` rather than re-imported — and asserts Property 28
holds for whatever response comes back. Handlers are awaited directly over a
constructed Starlette ``Request`` (the same technique the task-8.1 unit tests
use), so the property needs no HTTP client dependency.

Validates: Requirements 19.1, 19.2

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any, cast

from edu_it_api.app import create_app
from edu_it_api.errors.problem import PROBLEM_JSON_MEDIA_TYPE
from fastapi.exceptions import RequestValidationError
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

# The non-2xx HTTP status codes a boundary can realistically return: the full
# 4xx/5xx space (every standard client- and server-error status) plus a
# non-standard code (``418``) to prove consistency holds even for a status with
# no specific catalog entry. 2xx and 3xx codes are excluded — Property 28 only
# constrains non-2xx responses.
_NON_2XX_STATUSES: list[int] = sorted(
    {int(s) for s in HTTPStatus if not 200 <= int(s) < 300} | {418}
)


class _Body(BaseModel):
    """A minimal request body used to force a Pydantic v2 validation failure."""

    email: str
    count: int


# The one app whose registered handlers the property exercises; resolving them
# from this instance proves ``create_app`` wired exactly these handlers.
_APP = create_app()


def _handler_for(exc_type: type[Exception]) -> Any:
    """Return the handler ``create_app`` registered for ``exc_type``."""
    return _APP.exception_handlers[exc_type]


def _make_request() -> Request:
    """Build a minimal Starlette request the handlers can read a locale from."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/resource",
        "raw_path": b"/v1/resource",
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


def _validation_errors() -> list[dict[str, Any]]:
    """Produce a real Pydantic v2 error list from a malformed body."""
    try:
        _Body.model_validate({"email": 123, "count": "not-an-int"})
    except ValidationError as exc:
        return [dict(e) for e in exc.errors()]
    raise AssertionError("expected a ValidationError")


def _response_for(scenario: tuple[str, int]) -> Response:
    """Drive ``scenario`` through the app's registered handler and return the response."""
    kind, code = scenario
    request = _make_request()
    if kind == "raise":
        handler = _handler_for(StarletteHTTPException)
        exc: Exception = StarletteHTTPException(status_code=code)
    elif kind == "validate":
        handler = _handler_for(RequestValidationError)
        exc = RequestValidationError(_validation_errors())
    else:
        handler = _handler_for(Exception)
        exc = RuntimeError("SELECT * FROM users -- internal diagnostic")
    return cast(Response, asyncio.run(handler(request, exc)))


# A scenario is one of: raise an HTTPException with a drawn non-2xx status, a
# malformed body that fails validation (-> 422), or an unhandled error (-> 500).
# The union covers every way the boundary yields a non-2xx response.
_scenarios = st.one_of(
    st.sampled_from(_NON_2XX_STATUSES).map(lambda code: ("raise", code)),
    st.just(("validate", 0)),
    st.just(("boom", 0)),
)


@given(scenario=_scenarios)
@settings(max_examples=200)
def test_property_28_non_2xx_is_a_consistent_problem_document(
    scenario: tuple[str, int],
) -> None:
    """Feature: foundations, Property 28: Every non-2xx response is a problem document with a consistent status.

    For any request that yields a non-2xx response, the response is served as
    ``application/problem+json`` with at least ``type``, ``title``, and
    ``status``, and the body's ``status`` equals the response's HTTP status
    code.
    """
    response = _response_for(scenario)

    # Guard the premise: the property only constrains non-2xx responses.
    assert not 200 <= response.status_code < 300

    # Served as application/problem+json (Req 19.1). The media type may carry a
    # charset parameter, so compare the base media type.
    content_type = response.headers.get("content-type", "")
    assert content_type.split(";", 1)[0] == PROBLEM_JSON_MEDIA_TYPE

    body = json.loads(bytes(response.body))

    # Carries at least type, title, and status (Req 19.1).
    assert {"type", "title", "status"}.issubset(body)
    assert isinstance(body["type"], str) and body["type"]
    assert isinstance(body["title"], str) and body["title"]

    # The body's status equals the response's HTTP status code (Req 19.2).
    assert body["status"] == response.status_code
