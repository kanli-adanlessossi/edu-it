"""The console / retained-record ``Mailer`` fake (Req 5.2, 5.3).

:class:`Console_Mailer` implements the domain
:class:`~edu_it_domain.ports.mailer.Mailer` port for test and local development.
Each :meth:`send` writes the mail to standard output *and* retains it in an
in-memory record that a test can read back, so account flows (verification,
password reset, duplicate-sign-up notice) can assert exactly what mail was sent
without any SMTP server (Req 5.3, ``.kiro/steering/auth-privacy.md``).

The fake is substitutable for the production SMTP adapter without changing
domain code: it satisfies the same ``Mailer`` protocol and raises on a delivery
failure exactly as the port documents, so callers can keep an account in a safe
state and retry (Req 24).
"""

from __future__ import annotations

import sys
from typing import IO

from edu_it_domain.ports.mailer import Mailer, OutboundMail

__all__ = ["Console_Mailer"]


class Console_Mailer(Mailer):
    """A :class:`Mailer` that prints to a stream and retains every sent mail.

    Satisfies the :class:`~edu_it_domain.ports.mailer.Mailer` protocol, so domain
    code sends mail through :meth:`send` exactly as it does in production.
    """

    # The ``Mailer`` name has no ``Test`` prefix, but keep the collection marker
    # consistent with the other fakes so pytest never treats a fake as a test.
    __test__ = False

    def __init__(self, stream: IO[str] | None = None) -> None:
        """Create a mailer that writes to ``stream`` (default: standard output).

        The stream is resolved on each :meth:`send` when it defaults to standard
        output, so output follows a reassigned ``sys.stdout`` (as pytest's
        capture does).
        """
        self._stream = stream
        self._sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        """Write ``mail`` to the stream and retain it for inspection (Req 5.3).

        The mail is appended to the retained record only after it is written, so
        the record reflects messages that were emitted. A write failure
        propagates as an exception, leaving the mail out of the record, so the
        caller observes the delivery failure exactly as with the SMTP adapter
        (Req 24).
        """
        stream = self._stream if self._stream is not None else sys.stdout
        stream.write(self._render(mail))
        self._sent.append(mail)

    @property
    def sent(self) -> tuple[OutboundMail, ...]:
        """Every mail sent so far, in send order (a read-only snapshot)."""
        return tuple(self._sent)

    def sent_to(self, address: str) -> tuple[OutboundMail, ...]:
        """Every retained mail addressed to ``address``, in send order."""
        return tuple(mail for mail in self._sent if mail.to == address)

    def last(self) -> OutboundMail:
        """Return the most recently sent mail.

        Raises :class:`IndexError` when no mail has been sent, so a test that
        expects a message fails loudly rather than reading a stale one.
        """
        return self._sent[-1]

    def clear(self) -> None:
        """Discard the retained record so a test can start from a clean slate."""
        self._sent.clear()

    @staticmethod
    def _render(mail: OutboundMail) -> str:
        """Render ``mail`` as the human-readable block written to the stream."""
        lines = [
            "--- MAIL ---",
            f"To: {mail.to}",
            f"Subject: {mail.subject}",
        ]
        lines += [f"{name}: {value}" for name, value in mail.headers.items()]
        lines += ["", mail.body, "--- END MAIL ---", ""]
        return "\n".join(lines)
