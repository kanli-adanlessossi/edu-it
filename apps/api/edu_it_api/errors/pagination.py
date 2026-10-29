"""Map an invalid pagination cursor to a 400 RFC 9457 problem (task 18.2, Req 17.5).

The cursor's failure reason (malformed vs. expired) is only logged; the response
carries one translated, actionable ``detail`` telling the client to restart the
listing from the first page.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from edu_it_application.learning.pagination import InvalidCursorError

from edu_it_api.errors.catalog import ProblemType
from edu_it_api.errors.handlers import problem_response, request_locale
from edu_it_api.errors.problem import build_problem

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

__all__ = [
    "INVALID_CURSOR",
    "install_pagination_error_handlers",
]

_LOGGER = logging.getLogger("edu_it_api.errors.pagination")

INVALID_CURSOR: Final[ProblemType] = ProblemType(
    slug="invalid-cursor",
    status=int(HTTPStatus.BAD_REQUEST),
    title={"fr": "Curseur de pagination invalide", "en": "Invalid pagination cursor"},
    detail={
        "fr": "Le curseur de pagination est invalide ou a expiré. "
        "Relancez la liste depuis la première page.",
        "en": "The pagination cursor is invalid or has expired. "
        "Restart the list from the first page.",
    },
)


async def _handle_invalid_cursor(request: Request, exc: Exception) -> Response:
    _LOGGER.info("%s serving %s %s", exc, request.method, request.url.path)
    return problem_response(
        build_problem(
            status=INVALID_CURSOR.status,
            locale=request_locale(request),
            problem_type=INVALID_CURSOR,
            instance=request.url.path,
        )
    )


def install_pagination_error_handlers(app: FastAPI) -> None:
    """Register the invalid-cursor handler on ``app``."""
    app.add_exception_handler(InvalidCursorError, _handle_invalid_cursor)
