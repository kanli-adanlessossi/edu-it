"""Dependency reachability probes for the Readiness_Endpoint (task 9, Req 18.2, 18.3, 27.1, 27.3).

The readiness decision is the framework-free
:func:`edu_it_application.health.check_readiness`, which takes a set of
:class:`~edu_it_domain.ports.health.DependencyProbe` objects — each a name plus a
"is this reachable right now" callable — and reports ready iff all are
reachable. This module builds the concrete probes for the two datastores every
``apps/api`` / ``apps/worker`` instance requires: PostgreSQL (the system of
record) and Redis (session / queue / rate-limit state).

Each probe performs the cheapest possible liveness contact and returns a plain
boolean:

- **PostgreSQL** — open a short connection from the shared SQLAlchemy engine and
  issue ``SELECT 1``. A refused connection or any driver error raises, which
  :func:`~edu_it_application.health.check_readiness` treats as unreachable, so a
  down database yields a not-ready report rather than an exception.
- **Redis** — issue ``PING``. A truthy ``PONG`` means reachable; a connection
  error raises and is treated as unreachable.

The probes connect lazily on each call and cache nothing: readiness is always
recomputed against the live datastores, so a datastore that goes down makes the
next readiness check not-ready and one that comes back makes the next check
ready again (Req 27.3).

Probes are built from a :class:`~edu_it_infrastructure.composition.CompositionSettings`
so they target the same ``DATABASE_URL`` / ``REDIS_URL`` the rest of the adapters
use. The SQLAlchemy engine and Redis client are created lazily on first probe, so
constructing the probe set never requires a reachable datastore (a not-ready
report is the correct result when nothing is up yet, per Req 27.1).
"""

from __future__ import annotations

from edu_it_domain.ports.health import DependencyProbe
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.composition import CompositionSettings

__all__ = [
    "POSTGRES_DEPENDENCY",
    "REDIS_DEPENDENCY",
    "required_dependency_probes",
]

POSTGRES_DEPENDENCY = "postgresql"
"""The name reported for the PostgreSQL dependency in a not-ready readiness report."""

REDIS_DEPENDENCY = "redis"
"""The name reported for the Redis dependency in a not-ready readiness report."""


def required_dependency_probes(
    settings: CompositionSettings | None = None,
) -> tuple[DependencyProbe, ...]:
    """Build the readiness probes for the datastores api and worker require.

    Returns one probe per required dependency — PostgreSQL and Redis — targeting
    the ``DATABASE_URL`` / ``REDIS_URL`` in ``settings`` (read from the
    environment when omitted). The underlying engine and client are created
    lazily on first probe, so building the set never contacts a datastore; the
    first readiness check does.
    """
    settings = settings if settings is not None else CompositionSettings.from_env()
    postgres = _PostgresReachability(settings.database_url)
    redis = _RedisReachability(settings.redis_url)
    return (
        DependencyProbe(name=POSTGRES_DEPENDENCY, is_reachable=postgres.is_reachable),
        DependencyProbe(name=REDIS_DEPENDENCY, is_reachable=redis.is_reachable),
    )


class _PostgresReachability:
    """Probes PostgreSQL reachability with a short ``SELECT 1`` on a lazy engine."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        """Return the engine, creating it (and importing psycopg) on first probe."""
        if self._engine is None:
            self._engine = create_engine(self._database_url)
        return self._engine

    def is_reachable(self) -> bool:
        """Return whether PostgreSQL answers a trivial query right now.

        Opens a short connection and runs ``SELECT 1``. Returns ``True`` on a
        successful round trip; any failure (connection refused, driver error)
        propagates to :func:`~edu_it_application.health.check_readiness`, which
        treats it as unreachable.
        """
        with self._get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True


class _RedisReachability:
    """Probes Redis reachability with ``PING`` on a lazily built client."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: object | None = None

    def _get_client(self) -> object:
        """Return the Redis client, building it on first probe."""
        if self._client is None:
            self._client = redis_from_url(self._redis_url)
        return self._client

    def is_reachable(self) -> bool:
        """Return whether Redis answers ``PING`` right now.

        Returns ``True`` on a truthy ``PONG``; a connection error propagates to
        :func:`~edu_it_application.health.check_readiness`, which treats it as
        unreachable.
        """
        client = self._get_client()
        return bool(client.ping())  # type: ignore[attr-defined]
