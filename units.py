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
from edu_it_domain.ports.llm_provider import LLMProvider
from edu_it_domain.ports.mailer import Mailer
from edu_it_domain.ports.object_storage import ObjectStorage
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import SessionRepository, UserRepository
from edu_it_domain.ports.role_assignment import RoleAssignmentRepository
from edu_it_domain.ports.sandbox import SandboxProvider
from edu_it_domain.ports.session_store import SessionStore
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from edu_it_infrastructure.adapters.object_storage import FileSystem_ObjectStorage
from edu_it_infrastructure.adapters.rate_limiter import Redis_SignInRateLimiter
from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.mailer import Console_Mailer
from edu_it_infrastructure.fakes.object_storage import In_Memory_ObjectStorage
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.ledger_binding import (
    JobScope,
    LedgerAttemptGate,
    LedgerUsageSink,
)
from edu_it_infrastructure.llm.credentials import (
    CredentialSource,
    EnvironmentCredentialSource,
    SecretsManagerCredentialSource,
)
from edu_it_infrastructure.llm.live import LiveTransport, ModelPrice
from edu_it_infrastructure.llm.registry import get_vendor, load_vendors, registered_vendors
from edu_it_infrastructure.llm.replay import ReplayTransport
from edu_it_infrastructure.mailer import SMTP_Mailer, SMTPConfig
from edu_it_infrastructure.persistence.repositories import (
    Postgres_RoleAssignmentRepository,
    Postgres_SessionRepository,
    Postgres_UserRepository,
)

__all__ = [
    "CompositionSettings",
    "Environment",
    "LLMBackend",
    "LLMRole",
    "MailerBackend",
    "Ports",
    "ProviderSelectionError",
    "SandboxBackend",
    "build_fake_clock",
    "build_llm_provider",
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


class LLMBackend(StrEnum):
    """The ``LLMProvider`` selection (``LLM_PROVIDER_BACKEND``)."""

    FAKE = "fake"
    REPLAY = "replay"
    LIVE = "live"


class LLMRole(StrEnum):
    """Which configured model a provider is built for."""

    GENERATION = "generation"
    VERIFIER = "verifier"


class ProviderSelectionError(ValueError):
    """Raised at composition when the configured sandbox or LLM provider is not allowed."""


def _parse_csv(value: object) -> object:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return value


def _parse_prices(value: object) -> object:
    """Parse ``model=input:output,...`` (cents per million tokens) into a mapping."""
    if not isinstance(value, str):
        return value
    prices: dict[str, tuple[int, int]] = {}
    for part in (p.strip() for p in value.split(",")):
        if not part:
            continue
        try:
            model, rates = part.split("=", 1)
            cents_in, cents_out = rates.split(":", 1)
            prices[model.strip()] = (int(cents_in), int(cents_out))
        except ValueError:
            raise ValueError(
                "LLM_PRICES entries must look like model=input_cents:output_cents"
            ) from None
        if not model.strip() or min(prices[model.strip()]) < 0:
            raise ValueError("LLM_PRICES needs a model name and non-negative prices")
    return prices


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

    #: ``LLMProvider`` backend (``LLM_PROVIDER_BACKEND``): ``fake``, ``replay`` or ``live``.
    llm_provider_backend: LLMBackend = LLMBackend.FAKE
    #: Registered vendor adapter name (``LLM_PROVIDER_VENDOR``); optional if only one exists.
    llm_provider_vendor: str | None = None
    #: HTTPS endpoint of the live provider (``LLM_PROVIDER_ENDPOINT``).
    llm_provider_endpoint: str | None = None
    #: Credential of the live provider (``LLM_PROVIDER_API_KEY``); never printed.
    llm_provider_api_key: SecretStr | None = None
    #: Hosts a response may come from, EU only (``LLM_PROVIDER_ALLOWED_HOSTS``, comma-separated).
    llm_provider_allowed_hosts: tuple[str, ...] = ()
    #: Model for drafting (``LLM_MODEL_GENERATION``) and for the Verifier (``LLM_MODEL_VERIFIER``).
    llm_model_generation: str | None = None
    llm_model_verifier: str | None = None
    #: Cents per million tokens, ``model=input:output`` pairs (``LLM_PRICES``).
    llm_prices: dict[str, tuple[int, int]] = {}
    #: Root directory of recorded fixtures (``LLM_FIXTURE_DIR``).
    llm_fixture_dir: str = "evals/fixtures"
    #: Record live exchanges as fixtures (``LLM_RECORD``).
    llm_record: bool = False
    #: Secret key of the pseudonymous advisor handles (``ADVISOR_HANDLE_KEY``).
    advisor_handle_key: SecretStr | None = None

    @field_validator("llm_provider_allowed_hosts", mode="before")
    @classmethod
    def _split_allowed_hosts(cls, value: object) -> object:
        return _parse_csv(value)

    @field_validator("llm_prices", mode="before")
    @classmethod
    def _split_prices(cls, value: object) -> object:
        return _parse_prices(value)

    @field_validator(
        "llm_provider_vendor",
        "llm_provider_endpoint",
        "llm_model_generation",
        "llm_model_verifier",
        "llm_provider_api_key",
        "advisor_handle_key",
    )
    @classmethod
    def _empty_llm_setting_is_none(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            return value if value.get_secret_value() else None
        return value or None

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
        "llm_provider_backend": "LLM_PROVIDER_BACKEND",
        "llm_provider_vendor": "LLM_PROVIDER_VENDOR",
        "llm_provider_endpoint": "LLM_PROVIDER_ENDPOINT",
        "llm_provider_api_key": "LLM_PROVIDER_API_KEY",
        "llm_provider_allowed_hosts": "LLM_PROVIDER_ALLOWED_HOSTS",
        "llm_model_generation": "LLM_MODEL_GENERATION",
        "llm_model_verifier": "LLM_MODEL_VERIFIER",
        "llm_prices": "LLM_PRICES",
        "llm_fixture_dir": "LLM_FIXTURE_DIR",
        "llm_record": "LLM_RECORD",
        "advisor_handle_key": "ADVISOR_HANDLE_KEY",
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


def build_llm_provider(
    settings: CompositionSettings | None,
    job_scope: JobScope,
    *,
    role: LLMRole = LLMRole.GENERATION,
) -> LLMProvider:
    """Select the ``LLMProvider`` from configuration (ai-content Req 1.7, 2.1).

    Selection table (``EDU_IT_ENV`` by ``LLM_PROVIDER_BACKEND``):

    ============= ======= ======= =======
    environment   fake    replay  live
    ============= ======= ======= =======
    test          yes     yes     refused
    development   yes     yes     yes
    production    refused refused yes
    ============= ======= ======= =======

    A refusal raises :class:`ProviderSelectionError` before anything is
    constructed. Every provider is bound to ``job_scope``: the budget gate runs
    before each attempt and each attempt is recorded in the scope's ledger. The
    live backend resolves its vendor adapter through the registry; this module
    names no vendor.
    """
    settings = settings if settings is not None else CompositionSettings.from_env()
    backend = settings.llm_provider_backend
    env = settings.environment
    if backend is LLMBackend.LIVE and env is Environment.TEST:
        raise ProviderSelectionError(
            "refusing to start: LLM_PROVIDER_BACKEND=live cannot be used with EDU_IT_ENV=test"
        )
    if backend is not LLMBackend.LIVE and env.is_production:
        raise ProviderSelectionError(
            f"refusing to start: LLM_PROVIDER_BACKEND={backend.value} cannot be used "
            "with EDU_IT_ENV=production"
        )

    gate = LedgerAttemptGate(job_scope)
    sink = LedgerUsageSink(job_scope)
    is_verifier = role is LLMRole.VERIFIER
    model = settings.llm_model_verifier if is_verifier else settings.llm_model_generation

    if backend is LLMBackend.FAKE:
        return Fake_LLMProvider(gate=gate, sink=sink)
    if backend is LLMBackend.REPLAY:
        store = FixtureStore(Path(settings.llm_fixture_dir))
        return LLMEngine(
            ReplayTransport(store),
            model=model or "replay-model",
            gate=gate,
            sink=sink,
        )

    # Live: every setting it needs must be present; none is guessed.
    missing = [
        name
        for name, value in (
            ("LLM_PROVIDER_ENDPOINT", settings.llm_provider_endpoint),
            ("LLM_PROVIDER_API_KEY", settings.llm_provider_api_key),
            ("LLM_PROVIDER_ALLOWED_HOSTS", settings.llm_provider_allowed_hosts),
            ("LLM_MODEL_VERIFIER" if is_verifier else "LLM_MODEL_GENERATION", model),
            ("LLM_PRICES", settings.llm_prices),
        )
        if not value
    ]
    if missing:
        raise ProviderSelectionError("LLM_PROVIDER_BACKEND=live needs " + ", ".join(missing))
    assert settings.llm_provider_endpoint is not None  # narrowed by `missing`
    assert settings.llm_provider_api_key is not None
    assert model is not None
    api_key = settings.llm_provider_api_key.get_secret_value()

    vendor_name = settings.llm_provider_vendor
    if vendor_name is None:
        load_vendors()
        names = registered_vendors()
        if len(names) != 1:
            raise ProviderSelectionError(
                "LLM_PROVIDER_VENDOR must name one registered vendor "
                f"(registered: {', '.join(names) or 'none'})"
            )
        vendor_name = names[0]
    try:
        adapter = get_vendor(vendor_name)
    except LookupError as err:
        raise ProviderSelectionError(str(err)) from err

    allowed_hosts = frozenset(settings.llm_provider_allowed_hosts)
    store = FixtureStore(Path(settings.llm_fixture_dir))
    transport = LiveTransport(
        adapter=adapter,
        endpoint=settings.llm_provider_endpoint,
        api_key=api_key,
        allowed_hosts=allowed_hosts,
        prices={m: ModelPrice(i, o) for m, (i, o) in settings.llm_prices.items()},
        record_to=store if settings.llm_record else None,
    )
    return LLMEngine(
        transport,
        model=model,
        gate=gate,
        sink=sink,
        binding=ProviderBinding(allowed_hosts, lambda: transport.credential_id),
    )


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
