"""The composition root that assembles the domain ports (Req 5, 6.3).

This module is the single place that chooses *which* adapter implements each
domain port for a given environment and wires them into a :class:`Ports` bundle
the application layer consumes. It realizes the design's port table: production
uses the real adapters (the system :class:`~edu_it_infrastructure.clock.System_Clock`
and the SMTP :class:`~edu_it_infrastructure.mailer.SMTP_Mailer`), while test and
development use the in-process fakes
(:class:`~edu_it_infrastructure.fakes.clock.Test_Clock`,
:class:`~edu_it_infrastructure.fakes.mailer.Console_Mailer`, ...).

**Clock-setting surface in production (Req 6.3).** The controllable
``Test_Clock`` — the only implementation with a ``set``/``advance`` interface —
is wired **only** in the test/dev composition root. The production root wires the
``System_Clock``, which exposes no clock-setting interface, so production
registers no way to set or advance the clock. :func:`build_ports` enforces this
by construction: when the environment is ``production`` it never instantiates the
``Test_Clock``.

Settings are modelled with Pydantic v2 (allowed in the infrastructure layer; the
domain imports no framework) and populated from the process environment. The
environment-variable names match ``.env.example``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mailer import Mailer
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import SessionRepository, UserRepository
from edu_it_domain.ports.role_assignment import RoleAssignmentRepository
from edu_it_domain.ports.session_store import SessionStore
from pydantic import BaseModel, ConfigDict

from edu_it_infrastructure.adapters.rate_limiter import Redis_SignInRateLimiter
from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.mailer import Console_Mailer
from edu_it_infrastructure.mailer import SMTP_Mailer, SMTPConfig
from edu_it_infrastructure.persistence.repositories import (
    Postgres_RoleAssignmentRepository,
    Postgres_SessionRepository,
    Postgres_UserRepository,
)

__all__ = [
    "CompositionSettings",
    "Environment",
    "MailerBackend",
    "Ports",
    "build_ports",
]


class Environment(StrEnum):
    """The deployment environment selecting which adapters are wired.

    Values match ``EDU_IT_ENV`` in ``.env.example``. The controllable
    ``Test_Clock`` and the port fakes are wired only outside ``PRODUCTION``
    (Req 6.3).
    """

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"

    @property
    def is_production(self) -> bool:
        """True only for the production environment (fakes are disabled)."""
        return self is Environment.PRODUCTION


class MailerBackend(StrEnum):
    """The ``Mailer`` backend selection (``MAILER_BACKEND`` in ``.env.example``)."""

    CONSOLE = "console"
    SMTP = "smtp"


class CompositionSettings(BaseModel):
    """Environment-driven settings for the composition root.

    A Pydantic v2 model validating the environment-variable values defined in
    ``.env.example``; build it from the process environment with
    :meth:`from_env`. Only the settings needed to wire the ``Clock`` and
    ``Mailer`` ports are declared here; the remaining ports' settings are added
    as their adapters land.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    environment: Environment = Environment.DEVELOPMENT
    mailer_backend: MailerBackend = MailerBackend.CONSOLE

    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    mail_from_address: str = "no-reply@example.test"
    default_locale: str = "en"

    # Shared data-store connection URLs (``.env.example``). These back the
    # PostgreSQL identity repositories and the Redis session / rate-limit stores
    # (ADR 0004). The defaults are the local-dev URLs from ``.env.example``; the
    # clients are constructed lazily, so building the ports does not require a
    # live server.
    database_url: str = "postgresql://edu_it:change-me@localhost:5432/edu_it"
    redis_url: str = "redis://localhost:6379/0"

    # Maps each setting to its ``.env.example`` environment-variable name.
    _ENV_ALIASES: ClassVar[Mapping[str, str]] = {
        "environment": "EDU_IT_ENV",
        "mailer_backend": "MAILER_BACKEND",
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_username": "SMTP_USERNAME",
        "smtp_password": "SMTP_PASSWORD",
        "smtp_use_tls": "SMTP_USE_TLS",
        "mail_from_address": "MAIL_FROM_ADDRESS",
        "default_locale": "DEFAULT_LOCALE",
        "database_url": "DATABASE_URL",
        "redis_url": "REDIS_URL",
    }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CompositionSettings:
        """Build settings from ``env`` (defaults to the process environment).

        Only the variables that are present are read; any absent variable falls
        back to the field default, so an empty environment yields the dev
        defaults. Invalid values (e.g. an unknown ``EDU_IT_ENV``) raise a
        Pydantic validation error.
        """
        source = os.environ if env is None else env
        values = {
            field: source[var_name]
            for field, var_name in cls._ENV_ALIASES.items()
            if var_name in source
        }
        return cls.model_validate(values)


@dataclass(frozen=True, slots=True)
class Ports:
    """The assembled domain ports for one environment.

    The application layer depends on this bundle rather than on any concrete
    adapter, so swapping an environment's wiring changes no application code
    (Req 5.2). Only the ports wired by this task are present; more are added as
    their adapters land.
    """

    clock: Clock
    """The time source. ``System_Clock`` in production; ``Test_Clock`` otherwise."""

    mailer: Mailer
    """The outbound-mail port. ``SMTP_Mailer`` in production; ``Console_Mailer`` in dev/test."""

    user_repository: UserRepository
    """The durable ``user`` record store. ``Postgres_UserRepository`` (ADR 0004)."""

    session_repository: SessionRepository
    """The durable ``session`` record store (identity system of record, ADR 0004)."""

    session_store: SessionStore
    """The server-side ephemeral session store. ``Redis_SessionStore`` (ADR 0004)."""

    sign_in_rate_limiter: SignInRateLimiter
    """The sign-in failure rate limiter. ``Redis_SignInRateLimiter`` (Req 12)."""

    role_assignment_repository: RoleAssignmentRepository
    """The durable ``role_assignment`` store. ``Postgres_RoleAssignmentRepository`` (Req 13)."""


def build_ports(settings: CompositionSettings | None = None) -> Ports:
    """Assemble the :class:`Ports` bundle for the configured environment.

    When ``settings`` is omitted it is read from the environment. In
    ``production`` this wires the production adapters — the read-only
    :class:`System_Clock` and (for the ``smtp`` backend) the
    :class:`SMTP_Mailer` — and **never** instantiates the controllable
    ``Test_Clock``, so production registers no clock-setting interface (Req 6.3).
    In ``development``/``test`` it wires the fakes.

    The PostgreSQL identity repositories and the Redis session / rate-limit
    stores (ADR 0004) are built from ``DATABASE_URL`` / ``REDIS_URL``. Their
    SQLAlchemy engine and Redis client are constructed lazily and do not connect
    until first used, so assembling the ports never requires a live datastore —
    the Req 6.3 production guarantee (no clock-setting surface in production) is
    unaffected because the wired clock is still the read-only ``System_Clock``.
    """
    settings = settings if settings is not None else CompositionSettings.from_env()
    clock = _build_clock(settings)
    redis_client = redis_from_url(settings.redis_url)
    return Ports(
        clock=clock,
        mailer=_build_mailer(settings),
        # Pass the URL (not a built engine): the repositories create the engine —
        # and import the psycopg driver — only on first database use, so building
        # the ports needs no reachable PostgreSQL and no native libpq present.
        user_repository=Postgres_UserRepository(settings.database_url),
        session_repository=Postgres_SessionRepository(settings.database_url),
        session_store=Redis_SessionStore(redis_client, clock),
        sign_in_rate_limiter=Redis_SignInRateLimiter(redis_client, clock),
        role_assignment_repository=Postgres_RoleAssignmentRepository(settings.database_url),
    )


def _build_clock(settings: CompositionSettings) -> Clock:
    """Wire the ``Clock``: ``System_Clock`` in production, ``Test_Clock`` otherwise.

    Production never constructs the ``Test_Clock`` (its ``set``/``advance``
    surface would be a clock-setting interface), satisfying Req 6.3 by
    construction.
    """
    if settings.environment.is_production:
        return System_Clock()
    return Test_Clock()


def _build_mailer(settings: CompositionSettings) -> Mailer:
    """Wire the ``Mailer``: ``SMTP_Mailer`` for the smtp backend, else ``Console_Mailer``.

    The console fake is the default dev/test backend (it retains sent mail for
    inspection, Req 5.3); the SMTP adapter is selected for the ``smtp`` backend,
    which production uses.
    """
    if settings.mailer_backend is MailerBackend.SMTP:
        return SMTP_Mailer(
            SMTPConfig(
                host=settings.smtp_host,
                port=settings.smtp_port,
                from_address=settings.mail_from_address,
                username=settings.smtp_username,
                password=settings.smtp_password,
                use_tls=settings.smtp_use_tls,
                default_locale=settings.default_locale,
            )
        )
    return Console_Mailer()
