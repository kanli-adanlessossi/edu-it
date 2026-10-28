"""Classify datastore-unavailability failures (Req 27.2).

When PostgreSQL (the system of record) or Redis (session / queue / rate-limit
state) becomes unavailable *during a request*, the driver raises a connection or
operational error. The API boundary must turn such a failure into a translated
server-error Problem_Details while keeping the raw diagnostic (stack trace,
connection string, host identifier) out of the response body (Req 27.2).

Knowing *which* driver exceptions mean "the datastore is unavailable" is an
infrastructure concern — the concrete driver libraries (SQLAlchemy / psycopg for
PostgreSQL, ``redis`` for Redis) live in this layer, not at the API boundary.
:func:`is_datastore_unavailable` is the single place that encodes that knowledge
so the ``apps/api`` error handler can ask a framework-free question ("is this a
datastore outage?") without importing driver exception types itself.

The classifier matches the two datastores' *connection/operational* failure
families and deliberately does **not** match ordinary programming or
integrity errors (a bad query, a unique-constraint violation): those are genuine
bugs or 4xx conditions, not an unreachable datastore, and must keep flowing to
the generic handlers.
"""

from __future__ import annotations

import redis.exceptions as redis_exceptions
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
)

__all__ = ["DATASTORE_UNAVAILABLE_ERRORS", "is_datastore_unavailable"]

# SQLAlchemy raises ``OperationalError`` when the database cannot be contacted or
# drops mid-statement (connection refused, server shut down, network lost),
# ``InterfaceError`` when the driver connection itself is unusable, and
# ``DisconnectionError`` when the pool detects a dead connection. These are the
# PostgreSQL "unavailable during a request" family. ``DBAPIError`` — their common
# base — is deliberately **not** matched wholesale because ``ProgrammingError``
# and ``IntegrityError`` also derive from it and signal a bug or a 4xx condition,
# not an outage; those are classified separately via ``connection_invalidated``.
_POSTGRES_UNAVAILABLE: tuple[type[Exception], ...] = (
    OperationalError,
    InterfaceError,
    DisconnectionError,
)

# ``redis`` raises ``ConnectionError`` (refused / reset) and ``TimeoutError`` for
# an unreachable or unresponsive server; both derive from ``RedisError``. We key
# off the connection family specifically so a logical Redis error (e.g. a bad
# command) is not mistaken for an outage.
_REDIS_UNAVAILABLE: tuple[type[Exception], ...] = (
    redis_exceptions.ConnectionError,
    redis_exceptions.TimeoutError,
)

#: The exception types that mean "PostgreSQL or Redis is unavailable right now".
#: Exposed so callers (and tests) can reference the exact matched set.
DATASTORE_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    *_POSTGRES_UNAVAILABLE,
    *_REDIS_UNAVAILABLE,
)


def _is_invalidated_connection(exc: BaseException) -> bool:
    """Return whether ``exc`` is a DBAPIError flagged as a lost connection.

    SQLAlchemy sets ``connection_invalidated=True`` on a ``DBAPIError`` when it
    detects the underlying connection was dropped (so the pool discards it). That
    flag — not the error's subclass — is the reliable signal that the database
    became unreachable mid-statement, which is why it is checked here rather than
    matching ``DBAPIError`` by type.
    """
    return isinstance(exc, DBAPIError) and bool(getattr(exc, "connection_invalidated", False))


def is_datastore_unavailable(exc: BaseException) -> bool:
    """Return whether ``exc`` signals a datastore outage during a request (Req 27.2).

    ``True`` when ``exc`` — or any exception in its ``__cause__`` /
    ``__context__`` chain — is a PostgreSQL or Redis *connection/operational*
    failure (or a SQLAlchemy ``DBAPIError`` flagged ``connection_invalidated``).
    Walking the chain matters because adapters and SQLAlchemy often wrap the
    original driver error (``raise ... from err``); the outage is recognized even
    when it is the underlying cause rather than the outermost exception.

    Programming errors, integrity violations, and other logical faults are **not**
    matched: they are bugs or 4xx conditions, not an unreachable datastore, and
    must continue to the generic error handlers.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, DATASTORE_UNAVAILABLE_ERRORS) or _is_invalidated_connection(current):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
