"""The email verification-link consumption use case (task 12.2, Req 7.4, 7.5, 25.1, 25.2).

:class:`VerifyEmailUseCase` consumes the single-use Verification_Link a sign-up
emailed (task 12.1) and realizes the consumption behavior the design specifies
(design, "Sign-up and email verification"):

- Opening a **valid, unexpired, unused** link marks the owning user's email
  verified and invalidates the link, so a second use is treated as already used
  (Req 7.4, 25.1). This is the only state-changing path.
- Opening an **expired, already-used, or unrecognized** link verifies nothing,
  leaves the user's verification state unchanged, and returns a
  link-invalid/expired outcome that offers to resend the verification email
  (Req 7.5, 25.2).

Single use is enforced structurally. The link is marked consumed through the
verification-link repository's ``mark_consumed``, which transitions an unused
link to consumed atomically and returns ``False`` for an already-consumed link,
and the user transition goes through
:meth:`~edu_it_domain.ports.repository.UserRepository.mark_verified`, which is
itself idempotent. So replaying a link that succeeded once — or racing two
openings of the same link — verifies the email at most once and consumes the
link at most once; every later opening lands on the invalid/expired path (Req
7.4, 25.1).

Like the sign-up use case, this is deliberately framework-free: it imports only
the domain ports, records, and business constants, never a web or API framework
(Req 1.8). Expiry is decided solely by the injected ``Clock`` (Req 5.5, 7.3): the
use case compares the link against ``clock.now()`` through the link's own
:meth:`~edu_it_domain.ports.verification_link.VerificationLink.is_valid_at`
predicate, so the whole flow is deterministic under test.

The result type is a non-disclosing outcome: a successful verification and an
invalid/expired rejection are distinguished only by the :class:`VerifyEmailResult`
returned, and the rejection carries nothing about *why* the link failed (expired
vs. used vs. unrecognized), only that it is invalid or expired and that the
learner may request a fresh verification email. The ``apps/api`` boundary renders
this as a translated Problem_Details offering to resend (Req 25.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.repository import UserRepository
from edu_it_domain.ports.verification_link import VerificationLinkRepository

__all__ = [
    "VerifyEmailOutcome",
    "VerifyEmailRequest",
    "VerifyEmailResult",
    "VerifyEmailUseCase",
]


class VerifyEmailOutcome(Enum):
    """The two distinguishable outcomes of opening a verification link.

    The outcome is the only signal the boundary needs to decide what to render:
    a success page versus an invalid/expired page offering to resend (Req 25.2).
    The rejection reason (expired vs. used vs. unrecognized) is deliberately
    *not* exposed — the three are collapsed into one invalid/expired outcome so
    the response cannot disclose which failure occurred (Req 7.5, 25.2).
    """

    VERIFIED = "verified"
    """The link was valid, unexpired, and unused; the email is now verified."""

    INVALID_OR_EXPIRED = "invalid_or_expired"
    """The link was expired, already used, or unrecognized; nothing changed."""


@dataclass(frozen=True, slots=True)
class VerifyEmailRequest:
    """The verification attempt handed to :meth:`VerifyEmailUseCase.execute`.

    Carries only the opaque token the recipient opened. The token alone resolves
    the link; an unrecognized token is rejected as invalid/expired without
    disclosing anything further (Req 7.5).
    """

    token: str
    """The opaque single-use token carried by the verification email."""


@dataclass(frozen=True, slots=True)
class VerifyEmailResult:
    """The outcome of a verification-link consumption attempt (Req 7.4, 7.5).

    ``outcome`` is :attr:`VerifyEmailOutcome.VERIFIED` only when a valid,
    unexpired, unused link was consumed and its user marked verified;
    otherwise it is :attr:`VerifyEmailOutcome.INVALID_OR_EXPIRED` and
    :attr:`offer_resend` is ``True``, signalling the boundary to offer a fresh
    verification email (Req 25.2). On the invalid/expired path no user and no
    link were changed.
    """

    outcome: VerifyEmailOutcome
    """Whether the email was verified or the link was invalid/expired."""

    @property
    def is_verified(self) -> bool:
        """Whether the attempt verified the email (the success path)."""
        return self.outcome is VerifyEmailOutcome.VERIFIED

    @property
    def offer_resend(self) -> bool:
        """Whether the boundary should offer to resend verification (Req 25.2).

        True exactly on the invalid/expired path: an expired, used, or
        unrecognized link leaves the learner able to request a fresh email.
        """
        return self.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED


#: The single result every successful verification returns.
_VERIFIED_RESULT = VerifyEmailResult(outcome=VerifyEmailOutcome.VERIFIED)

#: The single result every expired/used/unrecognized link returns (Req 7.5,
#: 25.2). All three rejection causes return this exact value, so the response
#: never discloses which cause occurred and always offers to resend.
_INVALID_RESULT = VerifyEmailResult(outcome=VerifyEmailOutcome.INVALID_OR_EXPIRED)


class VerifyEmailUseCase:
    """Consume a verification link and verify the owning user's email (Req 7.4, 7.5)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        verification_links: VerificationLinkRepository,
        clock: Clock,
    ) -> None:
        """Wire the use case to its collaborators.

        Expiry is read only through ``clock``; the user and link stores enforce
        the single-use and idempotent transitions the outcome relies on.
        """
        self._users = users
        self._verification_links = verification_links
        self._clock = clock

    def execute(self, request: VerifyEmailRequest) -> VerifyEmailResult:
        """Consume the link named by ``request`` and return the outcome.

        Returns :attr:`VerifyEmailOutcome.VERIFIED` only when the token resolves
        to a link that is unexpired and unused as measured by the Clock, after
        marking the link consumed and the owning user verified (Req 7.4, 25.1).
        An unrecognized, expired, or already-used token changes no state and
        returns the invalid/expired result offering to resend (Req 7.5, 25.2).
        """
        link = self._verification_links.get(request.token)
        if link is None or not link.is_valid_at(self._clock.now()):
            return _INVALID_RESULT

        # Consume the link first: a successful mark_consumed is the single-use
        # gate. If it returns False the link was consumed between the lookup and
        # here (a concurrent opening won the race), so this opening is a
        # second use and must change nothing (Req 7.4, 25.1).
        if not self._verification_links.mark_consumed(request.token, consumed_at=self._clock.now()):
            return _INVALID_RESULT

        # The link's user is authoritative; mark_verified is idempotent, so a
        # user already verified leaves the state unchanged (Req 7.5).
        self._users.mark_verified(link.user_stable_id)
        return _VERIFIED_RESULT
