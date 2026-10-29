"""FastAPI exception handlers that render problem documents (Req 19, 7.8).

:func:`install_error_handlers` registers handlers on a FastAPI application so
that every non-2xx response leaves the boundary as an
``application/problem+json`` document (Req 19.1): a raised
:class:`~starlette.exceptions.HTTPException` becomes a status-typed problem, a
Pydantic v2 :class:`~fastapi.exceptions.RequestValidationError` becomes a
validation problem with one ``fields`` entry per rejected field (Req 7.8, 19.5),
and any otherwise-unhandled exception becomes a generic ``500`` problem whose
body carries no stack trace, SQL, or provider error (Req 19.6). Every handler
renders ``title``/``detail`` in the resolved request locale with a default-locale
fallback (Req 19.3, 19.4) and sets the body's ``status`` equal to the response's
HTTP status code (Req 19.2).

When PostgreSQL or Redis becomes unavailable *during a request*, the driver
raises a connection/operational error that would otherwise fall through to the
generic ``500`` handler. :func:`_handle_datastore_unavailable` intercepts that
specific failure family and renders a translated ``503 Service Unavailable``
problem — a server-error status that better signals a transient outage and
invites a retry — while logging the failure server-side. The body still carries
no stack trace, connection string, or host identifier (Req 27.2).

Internal diagnostics are logged, never returned: the unhandled-exception handler
logs the traceback through the standard logging module and returns only the safe,
localized problem body.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING

from edu_it_infrastructure.datastore_errors import (
    DATASTORE_UNAVAILABLE_ERRORS,
    is_datastore_unavailable,
)
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from edu_it_api.errors.catalog import VALIDATION, problem_type_for_status
from edu_it_api.errors.locale import resolve_locale
from edu_it_api.errors.problem import (
    PROBLEM_JSON_MEDIA_TYPE,
    ProblemDetails,
    build_problem,
    build_validation_problem,
)

if TYPE_CHECKING:
    from starlette.responses import Response

__all__ = ["install_error_handlers", "problem_response", "request_locale"]

# Non-exported handler callables (prefixed ``_``) are imported directly by the
# property/unit tests to drive each error path without a running server.

_LOGGER = logging.getLogger("edu_it_api.errors")


def request_locale(request: Request) -> str:
    """Resolve the locale for ``request`` (Req 19.3, 19.4).

    The explicit request Locale is read from the leading path segment when it is
    a supported locale (the ``/fr`` or ``/en`` route tree the web app uses) or
    from a ``locale`` query parameter the app may forward; the standard
    ``Accept-Language`` header is the fallback before the configured default.
    """
    segments = request.url.path.strip("/").split("/", 1)
    path_locale = segments[0] if segments and segments[0] else None
    query_locale = request.query_params.get("locale")
    return resolve_locale(
        explicit=query_locale or path_locale,
        accept_language=request.headers.get("accept-language"),
    )


def problem_response(problem: ProblemDetails) -> JSONResponse:
    """Serialize ``problem`` as an ``application/problem+json`` response.

    The HTTP status is taken from the problem's ``status`` member so the response
    status and body ``status`` always agree (Req 19.2).
    """
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_response_body(),
        media_type=PROBLEM_JSON_MEDIA_TYPE,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    """Render a raised ``HTTPException`` as a status-typed problem (Req 19.1)."""
    assert isinstance(exc, StarletteHTTPException)
    locale = request_locale(request)
    problem = build_problem(
        status=exc.status_code,
        locale=locale,
        problem_type=problem_type_for_status(exc.status_code),
        instance=request.url.path,
    )
    return problem_response(problem)


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    """Render a Pydantic v2 validation failure as a problem (Req 7.8, 19.5)."""
    assert isinstance(exc, RequestValidationError)
    locale = request_locale(request)
    problem = build_validation_problem(
        status=int(HTTPStatus.UNPROCESSABLE_ENTITY),
        locale=locale,
        errors=list(exc.errors()),
        problem_type=VALIDATION,
        instance=request.url.path,
    )
    return problem_response(problem)


def _server_error_problem(request: Request, status: int) -> Response:
    """Build a localized server-error problem for ``status`` (Req 19.3, 19.4)."""
    locale = request_locale(request)
    problem = build_problem(
        status=status,
        locale=locale,
        problem_type=problem_type_for_status(status),
        instance=request.url.path,
    )
    return problem_response(problem)


async def _handle_datastore_unavailable(request: Request, exc: Exception) -> Response:
    """Render a request-time PostgreSQL/Redis outage as a safe 503 (Req 27.2).

    The handler is registered for the datastore connection/operational exception
    families, but the same base types also cover logical driver faults (a bad
    query, a Redis command error) that are genuine bugs, not an outage. So it
    classifies the exception: a true datastore-unavailability yields a translated
    ``503 Service Unavailable`` problem inviting a retry; anything else is routed
    to the generic ``500`` path. In both cases the full exception (with its
    traceback, connection string, and host identifier) is logged for diagnostics
    and never reaches the response body.
    """
    if not is_datastore_unavailable(exc):
        return await _handle_unexpected_error(request, exc)
    _LOGGER.error(
        "datastore unavailable serving %s %s", request.method, request.url.path, exc_info=exc
    )
    return _server_error_problem(request, int(HTTPStatus.SERVICE_UNAVAILABLE))


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """Render any unhandled exception as a safe server error (Req 19.6, 27.2).

    An exception the datastore handlers were not registered for can still be a
    request-time outage surfacing as a connection-invalidated ``DBAPIError``;
    such a case is classified here and rendered as a ``503`` so it is not
    mislabeled ``500``. Every other unhandled exception becomes the generic
    ``500``. In both cases the full exception (with its traceback, SQL,
    connection string, and host identifier) is logged for diagnostics and the
    response body carries only the localized server-error problem.
    """
    if is_datastore_unavailable(exc):
        _LOGGER.error(
            "datastore unavailable serving %s %s", request.method, request.url.path, exc_info=exc
        )
        return _server_error_problem(request, int(HTTPStatus.SERVICE_UNAVAILABLE))
    _LOGGER.exception(
        "unhandled exception serving %s %s", request.method, request.url.path, exc_info=exc
    )
    return _server_error_problem(request, int(HTTPStatus.INTERNAL_SERVER_ERROR))


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem-document exception handlers on ``app`` (Req 19, 27.2).

    After this call, every non-2xx response the application produces for a
    raised ``HTTPException``, a request-validation failure, a request-time
    PostgreSQL/Redis outage, or any other unhandled exception is serialized as an
    ``application/problem+json`` document with a consistent ``status`` and
    locale-rendered text, and no internal diagnostics in the body. The datastore
    handlers are registered before the catch-all ``Exception`` handler so a
    connection/operational failure is classified into a ``503`` (Req 27.2) while
    an unrelated error still falls through to the generic ``500``.
    """
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    for datastore_error in DATASTORE_UNAVAILABLE_ERRORS:
        app.add_exception_handler(datastore_error, _handle_datastore_unavailable)
    # Imported lazily: the learning mapping reuses this module's response helpers.
    from edu_it_api.errors.learning import install_learning_error_handlers

    install_learning_error_handlers(app)
    app.add_exception_handler(Exception, _handle_unexpected_error)
