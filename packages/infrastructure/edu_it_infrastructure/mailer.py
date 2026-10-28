"""The production SMTP ``Mailer`` adapter (Req 5.1).

:class:`SMTP_Mailer` implements the domain
:class:`~edu_it_domain.ports.mailer.Mailer` port by transporting outbound mail
through an SMTP server, using Python's standard-library :mod:`smtplib` and
:mod:`email` (no third-party dependency; ADR 0001 pins only persistence
libraries for this layer). It is the production counterpart to the test/dev
:class:`~edu_it_infrastructure.fakes.mailer.Console_Mailer`, substitutable for it
without changing domain code: it satisfies the same ``Mailer`` protocol and
raises on a delivery failure exactly as the port documents, so a caller can keep
an account in a safe state, log the failure, and retry (Req 24).

**FR/EN templates (``.kiro/steering/auth-privacy.md``).** The domain hands this
adapter an already locale-rendered :class:`~edu_it_domain.ports.mailer.OutboundMail`
(subject and body). The adapter selects the message locale — ``fr`` or ``en`` —
from the ``OutboundMail`` headers (the port's documented locale/template marker),
falling back to a configured default locale when the mail carries none, and
stamps the outgoing MIME message with that locale (``Content-Language`` and the
localized ``Subject``/multipart envelope) so FR and EN mail are transported as
the correct locale. The two supported locales match the platform's ``fr``/``en``
surface.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from types import TracebackType
from typing import Protocol

from edu_it_domain.ports.mailer import Mailer, OutboundMail

__all__ = ["SMTPConfig", "SMTPConnection", "SMTP_DeliveryError", "SMTP_Mailer"]

# The platform's two supported locales (``.kiro/steering/auth-privacy.md``:
# FR/EN templates). A mail whose locale marker is neither of these is treated as
# carrying no locale and falls back to the configured default.
_SUPPORTED_LOCALES: frozenset[str] = frozenset({"fr", "en"})

# Headers the domain may use to mark the rendered locale of an OutboundMail. The
# mailer reads the first present marker, case-insensitively, to select FR/EN.
_LOCALE_HEADER_NAMES: tuple[str, ...] = ("locale", "content-language", "x-locale")


class SMTPConnection(Protocol):
    """The subset of the :class:`smtplib.SMTP` surface the mailer uses.

    Typing the connection as a Protocol (rather than :class:`smtplib.SMTP`)
    documents exactly the client behavior the adapter relies on and lets a test
    substitute an in-process transport without opening a socket.
    """

    def __enter__(self) -> SMTPConnection: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> object: ...

    def starttls(self) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, msg: EmailMessage) -> object: ...


class SMTPConnectionFactory(Protocol):
    """Builds an :class:`SMTPConnection` for a host/port with a timeout."""

    def __call__(self, host: str, port: int, *, timeout: float) -> SMTPConnection: ...


def _default_smtp_factory(host: str, port: int, *, timeout: float) -> SMTPConnection:
    """Open a real :class:`smtplib.SMTP` connection (the production transport)."""
    return smtplib.SMTP(host, port, timeout=timeout)


class SMTP_DeliveryError(RuntimeError):
    """A delivery attempt through SMTP failed (Req 24).

    Raised in place of the various :mod:`smtplib` exceptions so a caller catches
    a single adapter-level error, keeps the account in a safe state, logs the
    failure, and allows a retry — exactly the contract the ``Mailer`` port
    documents. The original transport error is chained as ``__cause__``.
    """


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    """Connection and envelope settings for :class:`SMTP_Mailer`.

    A framework-free settings value object (Pydantic-based settings may build it
    from the environment in the composition root). ``default_locale`` selects the
    FR/EN template locale for mail that carries no locale marker.
    """

    host: str
    """SMTP server hostname."""

    port: int
    """SMTP server port."""

    from_address: str
    """Envelope and ``From`` address applied to every outbound mail."""

    username: str | None = None
    """SMTP auth username, when the server requires authentication."""

    password: str | None = None
    """SMTP auth password, when the server requires authentication."""

    use_tls: bool = False
    """When true, upgrade the connection with STARTTLS before sending."""

    default_locale: str = "en"
    """Locale used to stamp mail that carries no explicit locale marker."""

    timeout: float = 30.0
    """Socket timeout, in seconds, for the SMTP connection."""


class SMTP_Mailer(Mailer):
    """A :class:`Mailer` that delivers mail over SMTP with FR/EN templates.

    Satisfies the :class:`~edu_it_domain.ports.mailer.Mailer` protocol, so domain
    code sends mail through :meth:`send` exactly as with the console fake. A
    delivery failure is surfaced as :class:`SMTP_DeliveryError` (Req 24).
    """

    def __init__(
        self,
        config: SMTPConfig,
        *,
        connection_factory: SMTPConnectionFactory | None = None,
    ) -> None:
        """Create a mailer bound to ``config``.

        ``connection_factory`` builds the SMTP client (defaults to a factory over
        :class:`smtplib.SMTP`); it is injectable so tests can substitute a fake
        transport without reaching a real server.
        """
        self._config = config
        self._connection_factory: SMTPConnectionFactory = (
            connection_factory if connection_factory is not None else _default_smtp_factory
        )

    def send(self, mail: OutboundMail) -> None:
        """Deliver ``mail`` over SMTP, selecting its FR/EN locale (Req 5.1).

        The message locale is read from ``mail.headers`` (``fr``/``en``),
        defaulting to the configured ``default_locale`` when absent or
        unsupported, and stamped on the outgoing message. Any transport failure
        is raised as :class:`SMTP_DeliveryError` so the caller keeps a safe state
        and may retry (Req 24).
        """
        message = self._build_message(mail)
        try:
            with self._connection_factory(
                self._config.host, self._config.port, timeout=self._config.timeout
            ) as client:
                if self._config.use_tls:
                    client.starttls()
                if self._config.username is not None and self._config.password is not None:
                    client.login(self._config.username, self._config.password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise SMTP_DeliveryError(
                f"failed to deliver mail to {mail.to!r} via "
                f"{self._config.host}:{self._config.port}: {exc}"
            ) from exc

    def _build_message(self, mail: OutboundMail) -> EmailMessage:
        """Render ``mail`` into a locale-stamped MIME :class:`EmailMessage`."""
        locale = self._resolve_locale(mail)
        message = EmailMessage()
        message["From"] = self._parse_address(self._config.from_address)
        message["To"] = self._parse_address(mail.to)
        message["Subject"] = mail.subject
        # Carry through any additional domain-supplied headers, except the locale
        # markers the adapter consumes to select the template.
        for name, value in mail.headers.items():
            if name.lower() in _LOCALE_HEADER_NAMES:
                continue
            message[name] = value
        # Set the body first: EmailMessage.set_content() clears existing
        # ``Content-*`` headers, so the locale stamp is applied afterward.
        message.set_content(mail.body)
        # Stamp the resolved FR/EN locale so the transported message declares the
        # language of its rendered content (FR/EN templates per auth-privacy).
        message["Content-Language"] = locale
        return message

    def _resolve_locale(self, mail: OutboundMail) -> str:
        """Return the FR/EN locale for ``mail``, defaulting when unmarked.

        The first recognized locale marker in the mail's headers wins; a missing
        or unsupported marker falls back to the configured ``default_locale``.
        """
        for raw_name, raw_value in mail.headers.items():
            if raw_name.lower() in _LOCALE_HEADER_NAMES:
                candidate = raw_value.strip().lower()
                if candidate in _SUPPORTED_LOCALES:
                    return candidate
        return self._config.default_locale

    @staticmethod
    def _parse_address(address: str) -> Address:
        """Parse ``address`` into an :class:`~email.headerregistry.Address`.

        Accepts a bare ``local@domain`` address (the form the domain supplies);
        a value without a single ``@`` is treated as having no domain so the
        resulting header is still well-formed.
        """
        local, _, domain = address.partition("@")
        return Address(username=local, domain=domain)
