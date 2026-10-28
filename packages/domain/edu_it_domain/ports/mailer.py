"""The ``Mailer`` port (Req 5.1).

Outbound mail (email verification, password reset, duplicate-sign-up notice)
leaves the domain through this port. Production uses an SMTP implementation; the
development/test fake writes mail to standard output and retains it in a
retrievable record for test inspection (Req 5.3). FR/EN templates are selected
by the concrete implementation per ``.kiro/steering/auth-privacy.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OutboundMail:
    """An email the domain hands to the :class:`Mailer` for delivery.

    A framework-free value object carrying only the fields the domain needs to
    describe a message; the concrete adapter renders and transports it.
    """

    to: str
    """Recipient email address."""

    subject: str
    """Rendered, locale-aware subject line."""

    body: str
    """Rendered, locale-aware message body."""

    headers: dict[str, str] = field(default_factory=dict)
    """Optional additional headers (e.g. a locale or template marker)."""


@runtime_checkable
class Mailer(Protocol):
    """Port for sending outbound mail."""

    def send(self, mail: OutboundMail) -> None:
        """Send ``mail``.

        A delivery failure is raised as an exception so the caller can keep the
        account in a safe state, log the failure, and allow a retry (Req 24).
        The development/test fake additionally retains each sent message for
        inspection (Req 5.3).
        """
        ...
