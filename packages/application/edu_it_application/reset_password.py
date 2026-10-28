"""The password-reset use cases (task 15.1, Req 9.1-9.6, 24.2, 24.3, 25.3, 28.1, 28.2).

This module holds the two framework-free use cases that realize the design's
password-reset behavior (design, "Password reset"), built on the ports from
prior tasks — the durable
:class:`~edu_it_domain.ports.repository.UserRepository` /
:class:`~edu_it_domain.ports.repository.SessionRepository`, the ephemeral
:class:`~edu_it_domain.ports.session_store.SessionStore` (ADR 0004), the
:class:`~edu_it_domain.ports.credential_store.CredentialStore`, the ``Mailer``,
and the new :class:`~edu_it_domain.ports.reset_link.ResetLinkRepository`:

- :class:`RequestPasswordResetUseCase` enqueues exactly one single-use reset
  email — whose link expires ``RESET_LINK_TTL`` (1 h) after issuance as measured
  by the injected ``Clock`` — for a **registered** email (Req 9.1), and enqueues
  **no** email while returning the byte-for-byte identical non-disclosing result
  for an **unregistered** email, so account existence is never disclosed (Req
  9.6). This mirrors sign-up's non-disclosing :data:`ACCEPTED` pattern. On a
  ``Mailer`` failure the password is not changed (none is on this path), the
  failure is logged with safe non-disclosing fields only, and
  :class:`PasswordResetMailDeliveryError` is raised so the boundary returns a
  translated Problem_Details and the learner may re-request (Req 24.2, 24.3),
  following sign-up's ``_send_or_raise`` retention/retry pattern.

- :class:`CompletePasswordResetUseCase` completes a reset on a **valid,
  unexpired, unused** link with a **policy-compliant** password: it updates the
  stored credential through :meth:`CredentialStore.set_password` (the raw secret
  is turned into the stored secret by an injected ``password_hasher``, so the raw
  secret never becomes the stored secret, exactly as sign-up does), marks the
  link consumed (the single-use gate, like
  :class:`~edu_it_application.verify_email.VerifyEmailUseCase`), revokes **all**
  existing Sessions through both the durable
  :meth:`SessionRepository.revoke_all_for_user` and the ephemeral
  ``SessionStore``, and establishes **exactly one** new Session for the device
  completing the reset — a ``SessionRecord`` plus ``SessionStore.create`` with
  expiry ``clock.now() + SESSION_DURATION`` (Req 9.5, 28.1, 28.2). A stale link —
  expired, already used, or unrecognized — leaves the password unchanged and
  returns a non-disclosing outcome offering to restart (Req 9.2, 9.3, 25.3). A
  weak password that fails the configured :class:`PasswordPolicy` leaves state
  unchanged and reports the policy failure (Req 9.4). Because the completed reset
  revokes every prior Session through ``revoke_all_for_user`` and the store, any
  Session it revoked thereafter resolves to nothing — rejected as
  unauthenticated and never refreshed or reissued, consistent with
  :class:`~edu_it_application.authenticate.AuthenticateSessionUseCase` (Req 28.1).

Like the other account use cases this is deliberately framework-free: it imports
only the domain ports, records, and business constants, never a web or API
framework (Req 1.8). Every non-deterministic input — the current time, the reset
token, the new session's opaque reference, and the password hash — enters
through an injected collaborator so the whole flow is deterministic under test
and carries no cryptography into the domain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from edu_it_domain.config import PASSWORD_POLICY, RESET_LINK_TTL, SESSION_DURATION, PasswordPolicy
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.mailer import Mailer, OutboundMail
from edu_it_domain.ports.repository import (
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.reset_link import ResetLink, ResetLinkRepository
from edu_it_domain.ports.session_store import SessionStore

__all__ = [
    "ACCEPTED",
    "CompletePasswordResetRequest",
    "CompletePasswordResetResult",
    "CompletePasswordResetUseCase",
    "PasswordResetMailDeliveryError",
    "RequestPasswordResetRequest",
    "RequestPasswordResetResult",
    "RequestPasswordResetUseCase",
    "ResetCompletionOutcome",
]

#: Application-layer logger for the password-reset flow. Records emitted through
#: the standard :mod:`logging` module are formatted as structured JSON carrying
#: the Request_ID by the ``apps/api`` logging foundation (task 8), with no
#: internal diagnostics reaching any response (Req 18.4, 18.6).
_LOGGER: Final[logging.Logger] = logging.getLogger("edu_it_application.reset_password")

#: Structured-log event recorded when the reset email fails to send (Req 24.2).
#: A stable string so log consumers can alert on it.
_MAIL_DELIVERY_FAILED_EVENT: Final[str] = "reset_password_email_delivery_failed"


# --- Reset request (Req 9.1, 9.6, 24.2, 24.3) --------------------------------


@dataclass(frozen=True, slots=True)
class RequestPasswordResetRequest:
    """A request to begin a password reset for an email (Req 9.1, 9.6)."""

    email: str
    """The email whose password reset is being requested."""


@dataclass(frozen=True, slots=True)
class RequestPasswordResetResult:
    """The non-disclosing outcome of a reset request (Req 9.6).

    The result is intentionally identical whether the email is registered or
    not, so it carries nothing that could disclose account existence: the only
    public signal is ``accepted``, always ``True`` for a well-formed request.
    The two internal paths (registered vs. unregistered) are deliberately
    indistinguishable from the returned value — which one ran is observable only
    through side effects (the reset mail sent), never through the response. This
    makes non-disclosure a property of the type itself rather than of careful
    caller discipline, exactly as the sign-up result does.
    """

    accepted: bool
    """Always ``True`` for a well-formed request (the non-disclosing signal)."""


#: The single accepted result every reset request returns, registered email or
#: not (Req 9.6). Both the registered and unregistered paths return this exact
#: value, so the response is byte-for-byte identical and cannot disclose whether
#: the email is registered.
ACCEPTED: Final[RequestPasswordResetResult] = RequestPasswordResetResult(accepted=True)


class PasswordResetMailDeliveryError(Exception):
    """Raised when the ``Mailer`` fails to deliver the reset email (Req 24.2).

    The failure is non-fatal to the account: no password change is made on the
    request path, so the user's existing password is untouched (Req 24.2), and
    the learner may simply request the reset again (Req 24.3). The triggering
    ``Mailer`` error is carried only as ``__cause__`` (for the logs), never in a
    field the boundary might echo, so no provider detail can leak into a response
    (Req 18.6, 19.6). The ``apps/api`` boundary maps this to a translated
    Problem_Details with a server-error status (Req 24.2).
    """

    def __init__(self) -> None:
        super().__init__("password-reset email could not be delivered")


class RequestPasswordResetUseCase:
    """Enqueue a single-use reset email for a registered email (Req 9.1, 9.6)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        reset_links: ResetLinkRepository,
        mailer: Mailer,
        clock: Clock,
        token_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``token_factory`` mints the opaque token for the new single-use link;
        issuance time and expiry are read through ``clock`` so the link expires
        exactly ``RESET_LINK_TTL`` (1 h) after issuance (Req 9.1), and the flow
        is deterministic under test.
        """
        self._users = users
        self._reset_links = reset_links
        self._mailer = mailer
        self._clock = clock
        self._token_factory = token_factory

    def execute(self, request: RequestPasswordResetRequest) -> RequestPasswordResetResult:
        """Enqueue a reset email for a registered email and return the result.

        For a registered email, stores one single-use reset link expiring 1 h
        after issuance per the Clock and sends the reset mail (Req 9.1). For an
        unregistered email, stores nothing and sends nothing, returning the
        identical :data:`ACCEPTED` result so account existence is never disclosed
        (Req 9.6). If the ``Mailer`` fails, no password is changed (none is on
        this path), the failure is logged, and
        :class:`PasswordResetMailDeliveryError` is raised so the learner can
        request the reset again (Req 24.2, 24.3).
        """
        user = self._users.get_by_email(request.email)
        if user is not None:
            self._issue_and_send(user)
        return ACCEPTED

    def _issue_and_send(self, user: UserRecord) -> None:
        """Store a fresh reset link for ``user`` then send it, logging failures.

        The link is stored **before** the mail is sent; if the ``Mailer`` fails,
        the user's password is untouched (nothing changed it), the failure is
        logged with non-disclosing structured fields — the user's ``stable_id``
        only, never the recipient address or the provider's error text — and
        :class:`PasswordResetMailDeliveryError` is raised with the original error
        chained as ``__cause__`` so the full diagnostic lives in the logs while
        the raised error carries nothing the boundary could echo (Req 24.2, 18.6).
        """
        issued_at = self._clock.now()
        link = ResetLink(
            token=self._token_factory(),
            user_stable_id=user.stable_id,
            issued_at=issued_at,
            expires_at=issued_at + RESET_LINK_TTL,
        )
        self._reset_links.add(link)

        try:
            self._mailer.send(_reset_mail(user, link))
        except Exception as error:
            _LOGGER.warning(
                "password-reset email delivery failed",
                extra={
                    "event": _MAIL_DELIVERY_FAILED_EVENT,
                    "user_stable_id": user.stable_id,
                },
            )
            raise PasswordResetMailDeliveryError from error


# --- Reset completion (Req 9.2-9.5, 25.3, 28.1, 28.2) ------------------------


class ResetCompletionOutcome(Enum):
    """The distinguishable outcomes of completing a password reset.

    The boundary uses the outcome to decide what to render: a success page, an
    invalid/expired page offering to restart, or a weak-password error. The
    *rejection* causes behind :attr:`INVALID_OR_EXPIRED` (expired vs. used vs.
    unrecognized) are deliberately collapsed into one outcome so the response
    cannot disclose which failure occurred (Req 9.3, 25.3).
    """

    COMPLETED = "completed"
    """The link was valid and the password satisfied the policy; the reset ran."""

    INVALID_OR_EXPIRED = "invalid_or_expired"
    """The link was expired, already used, or unrecognized; nothing changed."""

    WEAK_PASSWORD = "weak_password"
    """The link was valid but the new password failed the policy; nothing changed."""


@dataclass(frozen=True, slots=True)
class CompletePasswordResetRequest:
    """A reset-completion attempt handed to :meth:`CompletePasswordResetUseCase.execute`.

    Carries the opaque token the recipient opened and the new raw password. The
    token alone resolves the link; an unrecognized token is rejected as
    invalid/expired without disclosing anything further (Req 9.3).
    """

    token: str
    """The opaque single-use token carried by the reset email."""

    new_password: str
    """The submitted new raw password (validated, never stored, by this use case)."""


@dataclass(frozen=True, slots=True)
class CompletePasswordResetResult:
    """The outcome of a reset-completion attempt (Req 9.2-9.5).

    ``outcome`` is :attr:`ResetCompletionOutcome.COMPLETED` only when a valid,
    unexpired, unused link was consumed with a policy-compliant password, the
    password was updated, all prior Sessions revoked, and exactly one new Session
    established; it carries that new Session's opaque ``server_side_ref`` and the
    user's ``stable_id``. On the :attr:`ResetCompletionOutcome.INVALID_OR_EXPIRED`
    and :attr:`ResetCompletionOutcome.WEAK_PASSWORD` paths no password, link, or
    Session changed and both reference fields are ``None``.
    """

    outcome: ResetCompletionOutcome
    """Whether the reset completed, the link was invalid/expired, or the password was weak."""

    server_side_ref: str | None = None
    """The new Session's opaque reference on success, else ``None``."""

    user_stable_id: str | None = None
    """The ``stable_id`` of the user whose password was reset on success, else ``None``."""

    @property
    def is_completed(self) -> bool:
        """Whether the reset completed (the success path)."""
        return self.outcome is ResetCompletionOutcome.COMPLETED

    @property
    def offer_restart(self) -> bool:
        """Whether the boundary should offer to restart the reset (Req 25.3).

        True exactly on the invalid/expired path: an expired, used, or
        unrecognized link leaves the learner able to request a fresh reset.
        """
        return self.outcome is ResetCompletionOutcome.INVALID_OR_EXPIRED


#: The single result every expired/used/unrecognized link returns (Req 9.3,
#: 25.3). All three rejection causes return this exact value, so the response
#: never discloses which cause occurred and always offers to restart.
_INVALID_RESULT: Final[CompletePasswordResetResult] = CompletePasswordResetResult(
    outcome=ResetCompletionOutcome.INVALID_OR_EXPIRED
)

#: The single result every policy-failing password returns (Req 9.4). The link
#: was valid but the new password was too weak, so state is unchanged.
_WEAK_PASSWORD_RESULT: Final[CompletePasswordResetResult] = CompletePasswordResetResult(
    outcome=ResetCompletionOutcome.WEAK_PASSWORD
)


class CompletePasswordResetUseCase:
    """Complete a reset: set the password, revoke all sessions, establish one (Req 9.5)."""

    def __init__(
        self,
        *,
        reset_links: ResetLinkRepository,
        credentials: CredentialStore,
        sessions: SessionRepository,
        session_store: SessionStore,
        clock: Clock,
        password_hasher: Callable[[str], str],
        session_ref_factory: Callable[[], str],
        password_policy: PasswordPolicy = PASSWORD_POLICY,
    ) -> None:
        """Wire the use case to its collaborators.

        ``password_hasher`` turns the raw new password into the stored secret so
        the raw secret never becomes the stored secret (as sign-up does);
        ``session_ref_factory`` mints the new Session's opaque reference. Both are
        injected so the flow is deterministic under test and no cryptography or
        secret generation leaks into the domain. ``clock`` decides link expiry and
        sets the new Session's expiry at ``now + SESSION_DURATION`` (Req 9.5).
        ``password_policy`` defaults to the configured 12-128 character policy
        (Req 9.4) but is injectable for focused tests.
        """
        self._reset_links = reset_links
        self._credentials = credentials
        self._sessions = sessions
        self._session_store = session_store
        self._clock = clock
        self._password_hasher = password_hasher
        self._session_ref_factory = session_ref_factory
        self._password_policy = password_policy

    def execute(self, request: CompletePasswordResetRequest) -> CompletePasswordResetResult:
        """Complete the reset named by ``request`` and return the outcome.

        Returns :attr:`ResetCompletionOutcome.COMPLETED` only when the token
        resolves to a link that is unexpired and unused as measured by the Clock
        **and** the new password satisfies the policy: it updates the stored
        credential, marks the link consumed, revokes every prior Session, and
        establishes exactly one new Session for the completing device (Req 9.2,
        9.5, 28.1, 28.2). An unrecognized, expired, or already-used token changes
        no state and returns the invalid/expired result offering to restart (Req
        9.3, 25.3). A policy-failing password changes no state and reports the
        weak-password outcome (Req 9.4). The weak-password check runs before the
        link is consumed, so a learner who mistypes a weak password keeps the link
        usable for another attempt.
        """
        link = self._reset_links.get(request.token)
        if link is None or not link.is_valid_at(self._clock.now()):
            return _INVALID_RESULT

        # Reject a weak password *before* consuming the link, so a mistyped weak
        # password leaves both the password and the link unchanged and the
        # learner can retry the same link (Req 9.4).
        if not self._password_policy.is_satisfied_by(request.new_password):
            return _WEAK_PASSWORD_RESULT

        # Consume the link: a successful mark_consumed is the single-use gate. A
        # False return means the link was consumed between the lookup and here (a
        # concurrent completion won the race), so this attempt is a second use and
        # must change nothing (Req 9.2, 25.1).
        if not self._reset_links.mark_consumed(request.token, consumed_at=self._clock.now()):
            return _INVALID_RESULT

        return self._apply_reset(link.user_stable_id, request.new_password)

    def _apply_reset(self, user_stable_id: str, new_password: str) -> CompletePasswordResetResult:
        """Set the new password, revoke all sessions, and establish exactly one.

        The stored credential is replaced from the hashed new secret (the raw
        secret never becomes the stored secret, Req 9.2). Every prior Session is
        revoked through both the durable system of record
        (:meth:`SessionRepository.revoke_all_for_user`) and the ephemeral
        ``SessionStore`` so a Session revoked by the completed reset authenticates
        through neither and is never refreshed or reissued (Req 9.5, 28.1). Then a
        single new Session is established for the completing device — the durable
        record first, then the ephemeral state (ADR 0004) — expiring
        ``SESSION_DURATION`` after the current Clock instant, so the only valid
        Session afterward is the new one (Req 9.5, 28.2).
        """
        now = self._clock.now()
        self._credentials.set_password(user_stable_id, self._password_hasher(new_password))

        # Revoke every prior Session through both the durable system of record
        # (the identity system of record, ADR 0004) and the ephemeral fast-path
        # store that requests resolve against, so a Session revoked by the reset
        # authenticates through neither and none survives it (Req 9.5, 28.1).
        self._sessions.revoke_all_for_user(user_stable_id, revoked_at=now)
        self._session_store.revoke_all_for_user(user_stable_id)

        server_side_ref = self._session_ref_factory()
        expires_at = now + SESSION_DURATION
        self._sessions.add(
            SessionRecord(
                server_side_ref=server_side_ref,
                user_stable_id=user_stable_id,
                expires_at=expires_at,
            )
        )
        self._session_store.create(
            server_side_ref,
            user_stable_id,
            expires_at=expires_at,
        )
        return CompletePasswordResetResult(
            outcome=ResetCompletionOutcome.COMPLETED,
            server_side_ref=server_side_ref,
            user_stable_id=user_stable_id,
        )


def _reset_mail(user: UserRecord, link: ResetLink) -> OutboundMail:
    """Build the single-use password-reset email for ``user`` (Req 9.1).

    The mail is locale-marked so the ``Mailer`` renders the FR/EN template; the
    opaque token is carried so the reset-completion use case can resolve the link
    the recipient opens.
    """
    subject = "Réinitialisez votre mot de passe" if user.locale == "fr" else "Reset your password"
    body = (
        f"Bonjour, réinitialisez votre mot de passe avec ce lien à usage unique : {link.token}"
        if user.locale == "fr"
        else f"Hello, reset your password with this single-use link: {link.token}"
    )
    return OutboundMail(
        to=user.email,
        subject=subject,
        body=body,
        headers={"locale": user.locale, "X-Reset-Token": link.token},
    )
