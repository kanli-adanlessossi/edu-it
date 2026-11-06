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

import logging
from typing import Any

from edu_it_infrastructure import telemetry
from edu_it_infrastructure.telemetry import JsonLogFormatter

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


def generate_request_id() -> str:
    """Return a fresh, unique Request_ID (a UUID4 string).

    Used when an inbound request carries no usable Request_ID (Req 18.5;
    kubernetes-deployment Req 13.9).
    """
    return telemetry.generate_request_id()


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

    The variable is the shared telemetry one, so the task-queue decorator that
    writes the id into job envelopes (kubernetes-deployment Req 13.10) and the
    Worker's log records read the same value.
    """
    return telemetry.bind_request_id(request_id)


def reset_request_id(token: Any) -> None:
    """Restore the Request_ID context to the state captured by ``token``."""
    telemetry.reset_request_id(token)


def current_request_id() -> str | None:
    """Return the Request_ID bound to the current request, or ``None``."""
    return telemetry.current_request_id()


# The shared telemetry formatter: single-line JSON with `request_id` / `lab_session_id`
# and redaction of secrets and personal data (kubernetes-deployment Req 13.8, 13.11).


def configure_logging(*, level: int = logging.INFO) -> None:
    """Install the shared redacting JSON handler on the root logger (idempotent)."""
    telemetry.configure_logging(level=level)
