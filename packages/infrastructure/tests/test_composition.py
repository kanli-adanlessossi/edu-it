"""Unit tests for the composition root wiring (task 7.1, Req 5, 6.3).

These assertions pin the design's port table and the Req 6.3 production rule:

- Test/dev wire the fakes: the controllable ``Test_Clock`` and the
  ``Console_Mailer`` (which retains sent mail, Req 5.3).
- Production wires the real adapters: the read-only ``System_Clock`` and, for the
  ``smtp`` backend, the ``SMTP_Mailer``.
- Critically (Req 6.3): the production composition registers **no** clock-setting
  interface — the wired production clock has no ``set``/``advance`` surface, and
  the controllable ``Test_Clock`` is never instantiated for production.
- Settings are read from the environment-variable names defined in ``.env.example``.
"""

from __future__ import annotations

import pytest
from edu_it_domain.ports import (
    Clock,
    Mailer,
    SessionRepository,
    SessionStore,
    SignInRateLimiter,
    UserRepository,
)
from edu_it_infrastructure.adapters import Redis_SessionStore, Redis_SignInRateLimiter
from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    MailerBackend,
    Ports,
    build_ports,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.mailer import Console_Mailer
from edu_it_infrastructure.mailer import SMTP_Mailer
from edu_it_infrastructure.persistence.repositories import (
    Postgres_SessionRepository,
    Postgres_UserRepository,
)


def _settings(**overrides: object) -> CompositionSettings:
    return CompositionSettings(**overrides)  # type: ignore[arg-type]


# --- Req 5.2: the assembled bundle exposes the ports behind their protocols ---


@pytest.mark.parametrize("environment", list(Environment))
def test_build_ports_returns_ports_satisfying_the_domain_protocols(
    environment: Environment,
) -> None:
    ports = build_ports(_settings(environment=environment, mailer_backend=MailerBackend.CONSOLE))
    assert isinstance(ports, Ports)
    assert isinstance(ports.clock, Clock)
    assert isinstance(ports.mailer, Mailer)
    assert isinstance(ports.user_repository, UserRepository)
    assert isinstance(ports.session_repository, SessionRepository)
    assert isinstance(ports.session_store, SessionStore)
    assert isinstance(ports.sign_in_rate_limiter, SignInRateLimiter)


# --- Task 7.3: the identity repositories and Redis stores are wired (ADR 0004) ---


def test_build_ports_wires_the_postgres_repositories_and_redis_stores() -> None:
    # Built lazily, so no live PostgreSQL or Redis is needed to assemble them.
    ports = build_ports(_settings(environment=Environment.PRODUCTION))
    assert isinstance(ports.user_repository, Postgres_UserRepository)
    assert isinstance(ports.session_repository, Postgres_SessionRepository)
    assert isinstance(ports.session_store, Redis_SessionStore)
    assert isinstance(ports.sign_in_rate_limiter, Redis_SignInRateLimiter)


def test_settings_read_the_datastore_url_variable_names() -> None:
    settings = CompositionSettings.from_env(
        {
            "DATABASE_URL": "postgresql://u:p@db:5432/edu",
            "REDIS_URL": "redis://cache:6379/1",
        }
    )
    assert settings.database_url == "postgresql://u:p@db:5432/edu"
    assert settings.redis_url == "redis://cache:6379/1"


# --- Test/dev wire the fakes --------------------------------------------------


@pytest.mark.parametrize("environment", [Environment.DEVELOPMENT, Environment.TEST])
def test_test_and_dev_wire_the_test_clock_fake(environment: Environment) -> None:
    ports = build_ports(_settings(environment=environment))
    assert isinstance(ports.clock, Test_Clock)


def test_dev_default_mailer_is_the_console_fake_that_retains_mail() -> None:
    # Req 5.3: the dev Mailer retains sent mail for inspection.
    ports = build_ports(_settings(environment=Environment.DEVELOPMENT))
    assert isinstance(ports.mailer, Console_Mailer)


# --- Production wires the real adapters --------------------------------------


def test_production_wires_the_system_clock() -> None:
    ports = build_ports(_settings(environment=Environment.PRODUCTION))
    assert isinstance(ports.clock, System_Clock)


def test_production_smtp_backend_wires_the_smtp_mailer() -> None:
    ports = build_ports(
        _settings(environment=Environment.PRODUCTION, mailer_backend=MailerBackend.SMTP)
    )
    assert isinstance(ports.mailer, SMTP_Mailer)


# --- Req 6.3: production registers no clock-setting interface -----------------


def test_production_clock_has_no_set_or_advance_surface() -> None:
    # Req 6.3: the wired production clock exposes no interface that sets or
    # advances the clock.
    clock = build_ports(_settings(environment=Environment.PRODUCTION)).clock
    assert not hasattr(clock, "set")
    assert not hasattr(clock, "advance")


def test_production_clock_is_never_the_controllable_test_clock() -> None:
    # Req 6.3: the controllable Test_Clock is wired only in test/dev, never in
    # production.
    clock = build_ports(_settings(environment=Environment.PRODUCTION)).clock
    assert not isinstance(clock, Test_Clock)


# --- Settings read the .env.example variable names ----------------------------


def test_settings_from_env_reads_the_documented_variable_names() -> None:
    env = {
        "EDU_IT_ENV": "production",
        "MAILER_BACKEND": "smtp",
        "SMTP_HOST": "smtp.internal",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "user",
        "SMTP_PASSWORD": "pw",
        "SMTP_USE_TLS": "true",
        "MAIL_FROM_ADDRESS": "noreply@edu-it.test",
        "DEFAULT_LOCALE": "fr",
    }
    settings = CompositionSettings.from_env(env)
    assert settings.environment is Environment.PRODUCTION
    assert settings.mailer_backend is MailerBackend.SMTP
    assert settings.smtp_host == "smtp.internal"
    assert settings.smtp_port == 465
    assert settings.smtp_use_tls is True
    assert settings.mail_from_address == "noreply@edu-it.test"
    assert settings.default_locale == "fr"


def test_settings_from_empty_env_fall_back_to_dev_defaults() -> None:
    settings = CompositionSettings.from_env({})
    assert settings.environment is Environment.DEVELOPMENT
    assert settings.mailer_backend is MailerBackend.CONSOLE


def test_unknown_environment_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        CompositionSettings.from_env({"EDU_IT_ENV": "staging"})


def test_end_to_end_mailer_delivery_through_the_console_backend() -> None:
    # A dev-wired mailer delivers and retains mail (no SMTP server needed).
    ports = build_ports(_settings(environment=Environment.DEVELOPMENT))
    mailer = ports.mailer
    assert isinstance(mailer, Console_Mailer)
