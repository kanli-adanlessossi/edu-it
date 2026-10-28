"""Request-logging middleware binding a Request_ID to every request (Req 18.4, 18.5).

:class:`RequestLoggingMiddleware` resolves a Request_ID for each inbound request
(from the ``X-Request-ID`` header, or freshly generated when none is supplied,
per Req 18.5), binds it into the logging context so every log record emitted
while the request is handled carries it (Req 18.4), emits one structured JSON
access record per request, and echoes the Request_ID back on the response so a
caller can correlate it.

Only non-sensitive request metadata (method, path, status) is logged; internal
diagnostics stay in the logs and never leak into responses (Req 18.6).
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from edu_it_api.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    reset_request_id,
    resolve_request_id,
)

__all__ = ["RequestLoggingMiddleware"]

_logger = logging.getLogger("edu_it_api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Bind a Request_ID and emit a structured access log per request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
            _logger.info(
                "request.handled",
                extra={
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": response.status_code,
                },
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_request_id(token)
