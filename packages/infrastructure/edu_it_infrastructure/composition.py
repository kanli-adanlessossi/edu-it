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
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

from edu_it_domain.config import LAB_ALLOWED_WEB_ORIGINS
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mailer import Mailer
from edu_it_domain.ports.object_storage import ObjectStorage
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import SessionRepository, UserRepository
from edu_it_domain.ports.role_assignment import RoleAssignmentRepository
from edu_it_domain.ports.sandbox import SandboxProvider
from edu_it_domain.ports.session_store import SessionStore
from pydantic import BaseModel, ConfigDict, field_validator

from edu_it_infrastructure.adapters.object_storage import FileSystem_ObjectStorage
from edu_it_infrastructure.adapters.rate_limiter import Redis_SignInRateLimiter
from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.mailer import Console_Mailer
from edu_it_infrastructure.fakes.object_storage import In_Memory_ObjectStorage
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
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
    "ProviderSelectionError",
    "SandboxBackend",
    "build_fake_clock",
    "build_mailer",
    "build_object_storage",
    "build_ports",
    "build_sandbox_provider",
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


class SandboxBackend(StrEnum):
    """The ``SandboxProvider`` selection (``SANDBOX_PROVIDER``). Docker is dev only."""

    DOCKER = "docker"
    FAKE = "fake"


class ProviderSelectionError(ValueError):
    """Raised at composition when the configured sandbox provider is not allowed."""


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
    sandbox_provider: SandboxBackend = SandboxBackend.FAKE

    #: Tag of the locally built Lab_Images (``edu-it/lab-<image>:<tag>``), Docker provider only.
    lab_image_tag: str = "dev"
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

    #: Public base URL of the Web_App (``WEB_BASE_URL``); an absolute http(s) URL.
    web_base_url: str = "http://localhost:3000"
    #: ``Domain`` attribute of the session and ``csrf_token`` cookies
    #: (``COOKIE_DOMAIN``). ``None`` (unset or empty) gives host-only cookies.
    cookie_domain: str | None = None
    #: Origins allowed to call the API from a browser (CORS and terminal Origin check).
    allowed_web_origins: tuple[str, ...] = LAB_ALLOWED_WEB_ORIGINS

    #: GitHub OAuth application credentials and callback (``GITHUB_OAUTH_*``).
    #: ``None`` (unset or empty) means the variable is not configured.
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_callback_url: str | None = None

    #: ``ObjectStorage`` adapter (``OBJECT_STORAGE_BACKEND``): ``memory`` or ``filesystem``.
    #: Any other value is rejected by :func:`build_object_storage`, naming the adapter.
    object_storage_backend: str = "memory"
    #: Root directory of the ``filesystem`` backend (``OBJECT_STORAGE_ROOT``).
    object_storage_root: str | None = None

    @field_validator("github_client_id", "github_client_secret", "github_callback_url")
    @classmethod
    def _empty_github_setting_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("object_storage_root")
    @classmethod
    def _empty_object_storage_root_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("web_base_url")
    @classmethod
    def _web_base_url_is_absolute_http(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("web_base_url must be an absolute http or https URL")
        return value

    @field_validator("cookie_domain")
    @classmethod
    def _empty_cookie_domain_is_none(cls, value: str | None) -> str | None:
        return value or None

    # Maps each setting to its ``.env.example`` environment-variable name.
    _ENV_ALIASES: ClassVar[Mapping[str, str]] = {
        "environment": "EDU_IT_ENV",
        "mailer_backend": "MAILER_BACKEND",
        "sandbox_provider": "SANDBOX_PROVIDER",
        "lab_image_tag": "LAB_IMAGE_TAG",
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_username": "SMTP_USERNAME",
        "smtp_password": "SMTP_PASSWORD",
        "smtp_use_tls": "SMTP_USE_TLS",
        "mail_from_address": "MAIL_FROM_ADDRESS",
        "default_locale": "DEFAULT_LOCALE",
        "database_url": "DATABASE_URL",
        "redis_url": "REDIS_URL",
        "web_base_url": "WEB_BASE_URL",
        "cookie_domain": "COOKIE_DOMAIN",
        "github_client_id": "GITHUB_OAUTH_CLIENT_ID",
        "github_client_secret": "GITHUB_OAUTH_CLIENT_SECRET",
        "github_callback_url": "GITHUB_OAUTH_CALLBACK_URL",
        "object_storage_backend": "OBJECT_STORAGE_BACKEND",
        "object_storage_root": "OBJECT_STORAGE_ROOT",
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
        mailer=build_mailer(settings),
        # Pass the URL (not a built engine): the repositories create the engine —
        # and import the psycopg driver — only on first database use, so building
        # the ports needs no reachable PostgreSQL and no native libpq present.
        user_repository=Postgres_UserRepository(settings.database_url),
        session_repository=Postgres_SessionRepository(settings.database_url),
        session_store=Redis_SessionStore(redis_client, clock),
        sign_in_rate_limiter=Redis_SignInRateLimiter(redis_client, clock),
        role_assignment_repository=Postgres_RoleAssignmentRepository(settings.database_url),
    )


def build_sandbox_provider(
    settings: CompositionSettings | None = None,
    *,
    clock: Clock | None = None,
) -> SandboxProvider:
    """Select the ``SandboxProvider`` from configuration (Req 2.1, 2.2).

    Shared by api and worker composition. Raises :class:`ProviderSelectionError`
    when ``docker`` is selected with ``EDU_IT_ENV=production`` (the Docker
    provider is dev only), before any provider is constructed. ``fake`` is driven
    by a ``Test_Clock``: ``clock`` when it is one, otherwise a fresh one. Taking any
    ``Clock`` here keeps production app modules from importing ``Test_Clock``
    (Req 6.3).
    """
    if not isinstance(clock, Test_Clock):
        clock = None
    settings = settings if settings is not None else CompositionSettings.from_env()
    if settings.sandbox_provider is SandboxBackend.DOCKER:
        if settings.environment.is_production:
            raise ProviderSelectionError(
                "refusing to start: SANDBOX_PROVIDER=docker is dev only and "
                "cannot be used with EDU_IT_ENV=production"
            )
        from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

        return DockerSandboxProvider(image_tag=settings.lab_image_tag)
    return Fake_SandboxProvider(clock if clock is not None else Test_Clock())


def build_fake_clock() -> Clock:
    """Return a controllable ``Test_Clock`` typed as the read-only ``Clock`` port.

    For the in-memory fakes composition only (``build_adapters(fakes=True)``). It lives here,
    beside the other clock wiring, so production app modules never import ``Test_Clock``
    (Req 6.3); a test that needs ``advance`` reaches it on the returned object at run time.
    """
    return Test_Clock()


def _build_clock(settings: CompositionSettings) -> Clock:
    """Wire the ``Clock``: ``System_Clock`` in production, ``Test_Clock`` otherwise.

    Production never constructs the ``Test_Clock`` (its ``set``/``advance``
    surface would be a clock-setting interface), satisfying Req 6.3 by
    construction.
    """
    if settings.environment.is_production:
        return System_Clock()
    return Test_Clock()


def build_mailer(settings: CompositionSettings) -> Mailer:
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


def build_object_storage(settings: CompositionSettings) -> ObjectStorage:
    """Wire ``ObjectStorage`` from ``OBJECT_STORAGE_BACKEND`` (api-route-wiring Req 13.6).

    ``memory`` builds ``In_Memory_ObjectStorage``; ``filesystem`` builds
    ``FileSystem_ObjectStorage`` rooted at ``OBJECT_STORAGE_ROOT``. Any other value has no
    adapter and raises :class:`ProviderSelectionError` (the API stops the start, naming
    the adapter). The backend name in the message is a configuration value, never a secret.
    """
    backend = settings.object_storage_backend
    if backend == "memory":
        return In_Memory_ObjectStorage()
    if backend == "filesystem":
        if settings.object_storage_root is None:
            raise ProviderSelectionError(
                "OBJECT_STORAGE_BACKEND=filesystem needs OBJECT_STORAGE_ROOT"
            )
        return FileSystem_ObjectStorage(Path(settings.object_storage_root))
    raise ProviderSelectionError(f"no ObjectStorage adapter for OBJECT_STORAGE_BACKEND={backend!r}")
