"""Re-request a verification email (task 12.3, Req 24.3, 25.2).

:class:`ResendVerificationUseCase` is the "the User may request the email again"
half of the email-delivery-failure story (design, "Sign-up and email
verification"). It exists so a learner whose verification email failed to send
during sign-up (:class:`~edu_it_application.sign_up.SignUpMailDeliveryError`,
Req 24.1) — or whose link later expired or was used (Req 25.2) — can obtain a
fresh single-use link without starting a new sign-up and without us ever having
deleted their unverified account (Req 24.1, 24.3).

The use case mirrors the sign-up use case's two load-bearing properties:

- **Non-disclosure (Req 7.6-style).** Whatever the request's email maps to — an
  unverified user, an already-verified user, or no user at all — the returned
  value is the single, identical :data:`ACCEPTED` result. Only an *unverified*
  user actually has a new link stored and a new mail sent; an already-verified
  user and an unknown email change nothing. So the response cannot disclose
  whether the email is registered, let alone its verification state, exactly as
  a brand-new vs. duplicate sign-up cannot be told apart from the response.
- **Delivery-failure safety (Req 24.1, 24.3).** A fresh link is stored *before*
  the mail is sent; if the ``Mailer`` fails, the user stays unverified (it
  already was), the failure is logged with non-disclosing structured fields, and
  :class:`ResendVerificationError` is raised for the boundary to render as a
  translated Problem_Details. The learner may simply request again — the whole
  point of this use case — so a transient mail outage never traps an account.

Like the other account use cases this is framework-free: it imports only the
domain ports, records, and business constants, never a web or API framework
(Req 1.8). The new token, the issuance instant, and expiry are all injected or
read through the ``Clock``, so the flow is deterministic under test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from edu_it_domain.config import VERIFICATION_LINK_TTL
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mailer import Mailer, OutboundMail
from edu_it_domain.ports.repository import USER_STATUS_UNVERIFIED, UserRecord, UserRepository
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)

__all__ = [
    "ACCEPTED",
    "ResendVerificationError",
    "ResendVerificationRequest",
    "ResendVerificationResult",
    "ResendVerificationUseCase",
]

#: Application-layer logger for the resend flow. Records emitted through the
#: standard :mod:`logging` module are formatted as structured JSON carrying the
#: Request_ID by the ``apps/api`` logging foundation (task 8), with no internal
#: diagnostics reaching any response (Req 18.4, 18.6).
_LOGGER: Final[logging.Logger] = logging.getLogger("edu_it_application.resend_verification")

#: Structured-log event recorded when re-sending the verification email fails
#: (Req 24.1, 24.3). A stable string so log consumers can alert on it.
_MAIL_DELIVERY_FAILED_EVENT: Final[str] = "resend_verification_email_delivery_failed"


@dataclass(frozen=True, slots=True)
class ResendVerificationRequest:
    """A request to re-send the verification email for an address (Req 24.3)."""

    email: str
    """The email whose verification should be re-requested."""


@dataclass(frozen=True, slots=True)
class ResendVerificationResult:
    """The non-disclosing outcome of a resend request (Req 7.6-style).

    Carries only the always-``True`` ``accepted`` signal; which internal path ran
    (fresh link issued vs. nothing to do) is observable only through side effects
    (a new link, a new mail), never through this value, so the response cannot
    disclose whether the email is registered or its verification state.
    """

    accepted: bool
    """Always ``True`` for a well-formed request (the non-disclosing signal)."""


#: The single accepted result every resend request returns, whatever the email
#: maps to (Req 7.6-style non-disclosure).
ACCEPTED: Final[ResendVerificationResult] = ResendVerificationResult(accepted=True)


class ResendVerificationError(Exception):
    """Raised when re-sending the verification email fails to deliver (Req 24.1).

    As with sign-up, the account is left untouched (it stays unverified) and the
    triggering ``Mailer`` error is attached only as ``__cause__`` for the logs,
    never a field the boundary might echo (Req 18.6, 19.6). The ``apps/api``
    boundary maps this to a translated Problem_Details with a server-error status
    so the learner can try again (Req 24.3).
    """

    def __init__(self) -> None:
        super().__init__("verification email could not be re-delivered")


class ResendVerificationUseCase:
    """Issue a fresh verification link and re-send its email (Req 24.3, 25.2)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        verification_links: VerificationLinkRepository,
        mailer: Mailer,
        clock: Clock,
        token_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``token_factory`` mints the opaque token for the new single-use link;
        issuance time and expiry are read through ``clock`` so the link expires
        exactly ``VERIFICATION_LINK_TTL`` (24 h) after issuance (Req 7.3), and the
        flow is deterministic under test.
        """
        self._users = users
        self._verification_links = verification_links
        self._mailer = mailer
        self._clock = clock
        self._token_factory = token_factory

    def execute(self, request: ResendVerificationRequest) -> ResendVerificationResult:
        """Re-send the verification email and return the non-disclosing result.

        Issues a fresh single-use verification link and sends it only when the
        email maps to an existing **unverified** user; an already-verified user
        and an unknown email change nothing (Req 24.3). Either way the identical
        :data:`ACCEPTED` result is returned so account existence and verification
        state stay undisclosed. If the ``Mailer`` fails, the user remains
        unverified, the failure is logged, and :class:`ResendVerificationError`
        is raised so the learner can request again (Req 24.1, 24.3).
        """
        user = self._users.get_by_email(request.email)
        if user is None or user.status != USER_STATUS_UNVERIFIED:
            return ACCEPTED

        self._issue_and_send(user)
        return ACCEPTED

    def _issue_and_send(self, user: UserRecord) -> None:
        """Store a fresh link for ``user`` then send it, logging any failure."""
        issued_at = self._clock.now()
        link = VerificationLink(
            token=self._token_factory(),
            user_stable_id=user.stable_id,
            issued_at=issued_at,
            expires_at=issued_at + VERIFICATION_LINK_TTL,
        )
        self._verification_links.add(link)

        try:
            self._mailer.send(_verification_mail(user, link))
        except Exception as error:
            _LOGGER.warning(
                "resend verification email delivery failed",
                extra={
                    "event": _MAIL_DELIVERY_FAILED_EVENT,
                    "user_stable_id": user.stable_id,
                },
            )
            raise ResendVerificationError from error


def _verification_mail(user: UserRecord, link: VerificationLink) -> OutboundMail:
    """Build the single-use verification email for ``user`` (Req 7.1, 7.3).

    The mail is locale-marked so the ``Mailer`` renders the FR/EN template and
    carries the opaque token so the verification use case (task 12.2) can resolve
    the link the recipient opens.
    """
    subject = "Confirmez votre adresse e-mail" if user.locale == "fr" else "Confirm your email"
    body = (
        f"Bonjour, confirmez votre adresse e-mail avec ce lien à usage unique : {link.token}"
        if user.locale == "fr"
        else f"Hello, confirm your email with this single-use link: {link.token}"
    )
    return OutboundMail(
        to=user.email,
        subject=subject,
        body=body,
        headers={"locale": user.locale, "X-Verification-Token": link.token},
    )
