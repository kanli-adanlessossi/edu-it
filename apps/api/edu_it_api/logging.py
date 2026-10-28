"""Structured JSON logging with per-request Request_IDs (Req 18.4, 18.5, 18.6).

The API emits **structured JSON** log records (Req 18.4). Every record produced
while handling a request carries a ``Request_ID`` (Req 18.4): the inbound
``X-Request-ID`` header when the caller supplies one, otherwise a freshly
generated identifier (Req 18.5). The Request_ID is held in a
:class:`~contextvars.ContextVar` so any log call made anywhere in the request's
call stack picks it up without being threaded through function signatures.

Internal diagnostics (exception text, stack traces, SQL, provider errors) stay
in these logs and never reach a response body (Req 18.6, enforced at the error
boundary); this module is the "logs only" side of that split.

The formatter uses only the Python standard library: no logging framework is
pinned in ``docs/adr/0001-versions.md``, so adding one would introduce an
unpinned dependency. The record shape is deliberately small and stable so log
consumers can rely on it.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "REQUEST_ID_HEADER",
    "JsonLogFormatter",
    "bind_request_id",
    "configure_logging",
    "current_request_id",
    "generate_request_id",
    "resolve_request_id",
]

#: Inbound header carrying a caller-supplied Request_ID. When absent, the API
#: generates one (Req 18.5). ``X-Request-ID`` is the de-facto convention.
REQUEST_ID_HEADER = "x-request-id"

#: The maximum accepted length of an inbound Request_ID. Callers may supply an
#: arbitrary header value; cap it so a hostile or accidental megabyte-long header
#: cannot bloat every log record. Longer values are replaced by a generated id.
_MAX_REQUEST_ID_LENGTH = 128

#: Request-scoped Request_ID. ``None`` outside of a request (e.g. at startup), in
#: which case the formatter simply omits the field.
_request_id_var: ContextVar[str | None] = ContextVar("edu_it_request_id", default=None)

#: Reserved ``LogRecord`` attribute names. Anything a caller attaches via
#: ``logger.info(..., extra={...})`` that is *not* one of these is merged into
#: the JSON record as a structured field.
_RESERVED_RECORD_KEYS = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)


def generate_request_id() -> str:
    """Return a fresh, unique Request_ID.

    Used when an inbound request carries no usable Request_ID (Req 18.5).
    """
    return uuid.uuid4().hex


def resolve_request_id(inbound: str | None) -> str:
    """Resolve the Request_ID for a request from its inbound header value.

    Returns the inbound value when it is present and usable (non-blank and within
    :data:`_MAX_REQUEST_ID_LENGTH` after trimming surrounding whitespace, per
    Req 18.4); otherwise returns a freshly generated id (Req 18.5). This is a
    pure function so the resolution rule can be tested without an HTTP layer.
    """
    if inbound is not None:
        trimmed = inbound.strip()
        if trimmed and len(trimmed) <= _MAX_REQUEST_ID_LENGTH:
            return trimmed
    return generate_request_id()


def bind_request_id(request_id: str) -> object:
    """Bind ``request_id`` as the current request's Request_ID.

    Returns the :class:`~contextvars.Token` from the underlying ``ContextVar`` so
    the caller can restore the previous value with :func:`reset_request_id`.
    """
    return _request_id_var.set(request_id)


def reset_request_id(token: Any) -> None:
    """Restore the Request_ID context to the state captured by ``token``."""
    _request_id_var.reset(token)


def current_request_id() -> str | None:
    """Return the Request_ID bound to the current request, or ``None``."""
    return _request_id_var.get()


class JsonLogFormatter(logging.Formatter):
    """Format a :class:`logging.LogRecord` as a single-line JSON object.

    Every formatted record is valid JSON (Req 18.4) carrying the timestamp,
    level, logger name, and message. While a request is in flight the record also
    carries its ``request_id`` (Req 18.4); outside a request the field is omitted.
    Exception and stack information is rendered into the JSON (so internal
    diagnostics live in the logs, Req 18.6) rather than being dropped.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = current_request_id()
        if request_id is not None:
            payload["request_id"] = request_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # Merge any structured fields passed via ``extra=`` without letting them
        # overwrite the core fields above.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS and key not in payload:
                payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger's stream handler.

    Idempotent: repeated calls replace the root handlers with a single
    stream handler using :class:`JsonLogFormatter`, so every emitted record is
    structured JSON (Req 18.4) regardless of how many times the app is built.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
