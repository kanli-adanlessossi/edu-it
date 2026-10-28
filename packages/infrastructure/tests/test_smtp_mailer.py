"""Unit tests for the production SMTP_Mailer adapter (task 7.1, Req 5.1, 24).

Example and edge-case coverage without a real SMTP server: a fake connection
factory records the built message and the client calls, so we assert the adapter
satisfies the ``Mailer`` port, builds a locale-stamped MIME message selecting the
FR/EN template from the mail headers (with a configured default fallback),
performs STARTTLS and login only when configured, and raises
:class:`SMTP_DeliveryError` on a transport failure (Req 24).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from types import TracebackType
from typing import ClassVar

import pytest
from edu_it_domain.ports import Mailer, OutboundMail
from edu_it_infrastructure.mailer import SMTP_DeliveryError, SMTP_Mailer, SMTPConfig


class _FakeSMTP:
    """A stand-in for :class:`smtplib.SMTP` that records interactions.

    Instances are shared through the class-level ``instances`` list so a test can
    inspect the connection parameters, whether STARTTLS/login ran, and the
    message that was sent — all without opening a socket.
    """

    instances: ClassVar[list[_FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent_message: EmailMessage | None = None
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent_message = message


class _FailingSMTP(_FakeSMTP):
    """A fake transport whose ``send_message`` fails like a real SMTP error."""

    def send_message(self, message: EmailMessage) -> None:
        raise smtplib.SMTPException("transient delivery failure")


@pytest.fixture(autouse=True)
def _reset_instances() -> None:
    _FakeSMTP.instances.clear()


def _config(**overrides: object) -> SMTPConfig:
    base: dict[str, object] = {
        "host": "smtp.example.test",
        "port": 587,
        "from_address": "no-reply@example.test",
    }
    base.update(overrides)
    return SMTPConfig(**base)  # type: ignore[arg-type]


def _mailer(config: SMTPConfig | None = None) -> SMTP_Mailer:
    return SMTP_Mailer(config or _config(), connection_factory=_FakeSMTP)


def test_smtp_mailer_satisfies_the_domain_mailer_port() -> None:
    # Substitutable for the Console_Mailer fake without changing domain code (Req 5.2).
    assert isinstance(_mailer(), Mailer)


def test_send_delivers_a_message_with_from_to_and_subject() -> None:
    _mailer().send(OutboundMail(to="learner@example.com", subject="Verify", body="Hello"))
    [conn] = _FakeSMTP.instances
    assert conn.host == "smtp.example.test"
    assert conn.port == 587
    message = conn.sent_message
    assert message is not None
    assert message["To"] == "learner@example.com"
    assert message["Subject"] == "Verify"
    assert message["From"] == "no-reply@example.test"
    assert message.get_content().strip() == "Hello"


def test_locale_header_selects_the_fr_template() -> None:
    # FR/EN templates per auth-privacy: the locale marker selects the language.
    _mailer().send(
        OutboundMail(
            to="a@example.com", subject="Vérifiez", body="Bonjour", headers={"locale": "fr"}
        )
    )
    [conn] = _FakeSMTP.instances
    assert conn.sent_message is not None
    assert conn.sent_message["Content-Language"] == "fr"


def test_missing_locale_falls_back_to_the_configured_default() -> None:
    _mailer(_config(default_locale="en")).send(
        OutboundMail(to="a@example.com", subject="Verify", body="Hello")
    )
    [conn] = _FakeSMTP.instances
    assert conn.sent_message is not None
    assert conn.sent_message["Content-Language"] == "en"


def test_unsupported_locale_falls_back_to_the_default() -> None:
    _mailer(_config(default_locale="fr")).send(
        OutboundMail(to="a@example.com", subject="S", body="B", headers={"locale": "de"})
    )
    [conn] = _FakeSMTP.instances
    assert conn.sent_message is not None
    assert conn.sent_message["Content-Language"] == "fr"


def test_content_language_header_marker_is_also_honored() -> None:
    _mailer().send(
        OutboundMail(to="a@example.com", subject="S", body="B", headers={"Content-Language": "fr"})
    )
    [conn] = _FakeSMTP.instances
    assert conn.sent_message is not None
    assert conn.sent_message["Content-Language"] == "fr"


def test_additional_headers_are_carried_through_but_locale_markers_are_not_duplicated() -> None:
    _mailer().send(
        OutboundMail(
            to="a@example.com",
            subject="S",
            body="B",
            headers={"locale": "fr", "X-Template": "verification"},
        )
    )
    [conn] = _FakeSMTP.instances
    message = conn.sent_message
    assert message is not None
    assert message["X-Template"] == "verification"
    # The consumed locale marker is not re-emitted as a raw header.
    assert message.get_all("locale") is None
    # Exactly one Content-Language header is present.
    assert message.get_all("Content-Language") == ["fr"]


def test_starttls_and_login_run_only_when_configured() -> None:
    _mailer(_config(use_tls=True, username="mailer-user", password="secret")).send(
        OutboundMail(to="a@example.com", subject="S", body="B")
    )
    [conn] = _FakeSMTP.instances
    assert conn.started_tls is True
    assert conn.login_args == ("mailer-user", "secret")


def test_no_starttls_or_login_without_credentials() -> None:
    _mailer().send(OutboundMail(to="a@example.com", subject="S", body="B"))
    [conn] = _FakeSMTP.instances
    assert conn.started_tls is False
    assert conn.login_args is None


def test_delivery_failure_is_raised_as_smtp_delivery_error() -> None:
    # Req 24: a transport failure surfaces as a single adapter error the caller
    # can catch to keep a safe state and retry; the cause is chained.
    mailer = SMTP_Mailer(_config(), connection_factory=_FailingSMTP)
    with pytest.raises(SMTP_DeliveryError) as excinfo:
        mailer.send(OutboundMail(to="a@example.com", subject="S", body="B"))
    assert isinstance(excinfo.value.__cause__, smtplib.SMTPException)
