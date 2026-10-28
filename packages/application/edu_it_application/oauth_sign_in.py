"""The GitHub OAuth callback flow and account-linking state machine (task 16.1).

Requirements 10.1-10.7, 26.1-26.6.

This module holds the three framework-free use cases that realize the design's
"GitHub OAuth and account linking" state machine (design, Req 10, 26), built on
the ports from prior tasks — the durable
:class:`~edu_it_domain.ports.repository.UserRepository` /
:class:`~edu_it_domain.ports.repository.SessionRepository`, the ephemeral
:class:`~edu_it_domain.ports.session_store.SessionStore` (ADR 0004) — and the two
new OAuth ports: the durable
:class:`~edu_it_domain.ports.oauth_link.OAuthLinkRepository` and the transient
:class:`~edu_it_domain.ports.pending_oauth_link.PendingOAuthLinkRepository`.

The state machine the design draws (design, "GitHub OAuth and account linking")
is driven entirely by :class:`CompleteOAuthCallbackUseCase.execute`, with the
pending-confirmation branch completed by :class:`ConfirmOAuthLinkUseCase` and
:class:`DeclineOAuthLinkUseCase`:

- **Rejected** — a callback whose ``state`` does not match the issued one (Req
  26.6), a flow the visitor denied, a provider failure, or an incomplete flow
  creates no User, ``oauth_link``, or Session, discards any partial state, and
  raises :class:`OAuthCallbackError` with :attr:`OAuthCallbackFailure.REJECTED`
  (Req 10.5, 26.1).
- **Email unverified** — a callback whose GitHub email is not verified at GitHub
  links nothing and raises :class:`OAuthCallbackError` with
  :attr:`OAuthCallbackFailure.EMAIL_UNVERIFIED`, leaving any existing User's
  records unchanged (Req 10.3, 26.3).
- **Existing link** — when an ``oauth_link`` already exists for the returned
  ``(provider, subject)`` the linked User is signed in with **no** duplicate
  account created (Req 10.4).
- **No match** — a verified email matching no existing User creates exactly one
  User, records the ``oauth_link``, and establishes a Session (Req 10.1).
- **Pending confirmation** — a verified email matching an existing User presents
  a link-confirmation step: a single-use pending record is stored and a
  **temporary, unauthenticated** session is established that lets the visitor
  complete the in-app confirmation but grants no authenticated access to the
  existing User until the link is confirmed (Req 10.2, 10.7, 26.2).

:class:`ConfirmOAuthLinkUseCase` makes the ``oauth_link`` and establishes an
authenticated Session **only** when the pending record is unconsumed and still
inside the confirmation window measured by the ``Clock`` (Req 10.2, 26.2); an
expired or already-consumed token links nothing and leaves the existing User's
records unchanged (Req 10.6, 26.5). :class:`DeclineOAuthLinkUseCase` invalidates
the pending token *immediately* so it can never be confirmed later, and makes no
link (Req 10.6, 26.4).

Like the other account use cases this is deliberately framework-free: it imports
only the domain ports, records, and business constants, never a web or API
framework (Req 1.8). Every non-deterministic input — the current time, the new
user's ``stable_id``, the pending-confirmation token, and the new Session's
opaque reference — enters through an injected collaborator, so the whole flow is
deterministic under test. The provider handshake itself (redirect, token
exchange, ``state`` issuance and verification) happens at the ``apps/api``
boundary against a mock provider in tests (Req 21.5); this use case consumes the
*result* of that handshake as a :class:`OAuthCallback` value object, so the
linking logic stays free of any HTTP or provider-SDK coupling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from edu_it_domain.config import OAUTH_LINK_CONFIRMATION_WINDOW, SESSION_DURATION
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB, OAuthLink, OAuthLinkRepository
from edu_it_domain.ports.pending_oauth_link import (
    PendingOAuthLink,
    PendingOAuthLinkRepository,
)
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.session_store import SessionStore

__all__ = [
    "CompleteOAuthCallbackUseCase",
    "ConfirmOAuthLinkRequest",
    "ConfirmOAuthLinkUseCase",
    "DeclineOAuthLinkRequest",
    "DeclineOAuthLinkResult",
    "DeclineOAuthLinkUseCase",
    "OAuthCallback",
    "OAuthCallbackError",
    "OAuthCallbackFailure",
    "OAuthCallbackResult",
    "OAuthCallbackStatus",
    "OAuthLinkConfirmationOutcome",
    "OAuthLinkConfirmationResult",
]

#: The locale assigned to a User created through the GitHub OAuth flow. The
#: callback carries no locale preference, so a new OAuth user defaults to English
#: and can change it later through the language-preference flow (Req 15.3).
_DEFAULT_OAUTH_LOCALE = "en"


# --- The callback value object (the handshake result) ------------------------


@dataclass(frozen=True, slots=True)
class OAuthCallback:
    """The result of a completed GitHub OAuth handshake handed to the use case.

    The ``apps/api`` boundary performs the provider handshake (redirect, token
    exchange, ``state`` issuance) — against a mock provider in tests (Req 21.5) —
    and reduces its outcome to this framework-free value object, so the linking
    state machine depends on no HTTP or provider SDK.

    A callback is only a candidate for linking when it both **completed** and its
    ``state`` **matched** the issued one; the boundary sets :attr:`completed` and
    :attr:`state_matches` accordingly. A denied, failed, or incomplete flow
    arrives with ``completed`` false; a forged or stale callback arrives with
    ``state_matches`` false (Req 26.1, 26.6). When the flow did complete,
    :attr:`subject`, :attr:`email`, and :attr:`email_verified` carry what GitHub
    returned.
    """

    completed: bool
    """Whether the provider flow completed (``False`` when denied/failed/incomplete)."""

    state_matches: bool
    """Whether the returned ``state`` matched the issued one (Req 26.6)."""

    subject: str
    """GitHub's stable account identifier for the visitor (valid when completed)."""

    email: str
    """The primary email GitHub returned for the account (valid when completed)."""

    email_verified: bool
    """Whether GitHub reports the email as verified (Req 10.3, 26.3)."""

    provider: str = PROVIDER_GITHUB
    """The OAuth provider; GitHub this phase."""


# --- Callback outcome types --------------------------------------------------


class OAuthCallbackStatus(Enum):
    """The success outcomes of a completed, valid OAuth callback.

    A callback that is rejected or whose email is unverified raises
    :class:`OAuthCallbackError` instead of returning one of these; a returned
    :class:`OAuthCallbackResult` therefore always describes a flow that reached
    a state-changing success path.
    """

    SIGNED_IN = "signed_in"
    """A Session was established — a new linked account or an existing link (Req 10.1, 10.4)."""

    PENDING_CONFIRMATION = "pending_confirmation"
    """A verified email matched an existing User; confirmation is pending (Req 10.2, 10.7)."""


class OAuthCallbackFailure(Enum):
    """Why an OAuth callback was refused (the two translated Problem_Details).

    - :attr:`REJECTED` — the flow was denied, failed at the provider, did not
      complete, or returned a mismatched ``state``; no User, ``oauth_link``, or
      Session is created and any partial state is discarded (Req 10.5, 26.1,
      26.6). The distinct rejection causes are deliberately collapsed into one
      reason so the response does not disclose which occurred.
    - :attr:`EMAIL_UNVERIFIED` — the flow completed but GitHub reports the email
      unverified; nothing is linked and any existing User's records are unchanged
      (Req 10.3, 26.3).
    """

    REJECTED = "rejected"
    """Denied, provider failure, incomplete, or ``state`` mismatch (Req 10.5, 26.1, 26.6)."""

    EMAIL_UNVERIFIED = "email_unverified"
    """The flow completed but the GitHub email is unverified (Req 10.3, 26.3)."""


class OAuthCallbackError(Exception):
    """Raised when an OAuth callback is refused, carrying a coarse reason (Req 10.3, 10.5).

    :attr:`failure` is the :class:`OAuthCallbackFailure` the ``apps/api`` boundary
    maps to a translated Problem_Details (Req 10.3, 10.5, 26.1). The error is
    raised **before** any User, ``oauth_link``, or Session is created, so a
    refused callback changes no state and discards any partial authentication
    state (Req 26.1, 26.6).
    """

    def __init__(self, failure: OAuthCallbackFailure) -> None:
        self.failure: OAuthCallbackFailure = failure
        super().__init__(f"oauth callback refused: {failure.value}")


@dataclass(frozen=True, slots=True)
class OAuthCallbackResult:
    """The outcome of a completed, valid OAuth callback (Req 10.1, 10.2, 10.4).

    On :attr:`OAuthCallbackStatus.SIGNED_IN` it carries the established Session's
    opaque ``server_side_ref`` and the signed-in user's ``stable_id``; the
    pending fields are ``None``.

    On :attr:`OAuthCallbackStatus.PENDING_CONFIRMATION` it carries the single-use
    ``pending_token`` the temporary unauthenticated session holds and the opaque
    ``temporary_session_ref`` of that session (Req 10.7); the signed-in fields are
    ``None``. The temporary session grants no authenticated access to the existing
    User — only the confirm step does (Req 10.7).
    """

    status: OAuthCallbackStatus
    """Whether the visitor was signed in or a confirmation is pending."""

    server_side_ref: str | None = None
    """The authenticated Session's opaque reference on sign-in, else ``None``."""

    user_stable_id: str | None = None
    """The signed-in user's ``stable_id`` on sign-in, else ``None``."""

    pending_token: str | None = None
    """The single-use confirmation token when a link is pending, else ``None``."""

    temporary_session_ref: str | None = None
    """The temporary unauthenticated session's reference when pending, else ``None``."""

    @property
    def is_signed_in(self) -> bool:
        """Whether an authenticated Session was established (the sign-in path)."""
        return self.status is OAuthCallbackStatus.SIGNED_IN

    @property
    def is_pending_confirmation(self) -> bool:
        """Whether a link confirmation is pending (the existing-User path)."""
        return self.status is OAuthCallbackStatus.PENDING_CONFIRMATION


class CompleteOAuthCallbackUseCase:
    """Drive the OAuth callback state machine from a completed handshake (Req 10, 26)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        oauth_links: OAuthLinkRepository,
        pending_links: PendingOAuthLinkRepository,
        sessions: SessionRepository,
        session_store: SessionStore,
        clock: Clock,
        stable_id_factory: Callable[[], str],
        pending_token_factory: Callable[[], str],
        session_ref_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``stable_id_factory`` mints a new user's ``stable_id`` on the no-match
        path; ``pending_token_factory`` mints the single-use confirmation token;
        ``session_ref_factory`` mints both the authenticated Session reference
        (sign-in paths) and the temporary unauthenticated session reference
        (pending path). All three are injected so the flow is deterministic under
        test and no secret generation leaks into the domain. ``clock`` sets the
        Session's expiry at ``now + SESSION_DURATION`` and the pending record's
        window at ``now + OAUTH_LINK_CONFIRMATION_WINDOW``.
        """
        self._users = users
        self._oauth_links = oauth_links
        self._pending_links = pending_links
        self._sessions = sessions
        self._session_store = session_store
        self._clock = clock
        self._stable_id_factory = stable_id_factory
        self._pending_token_factory = pending_token_factory
        self._session_ref_factory = session_ref_factory

    def execute(self, callback: OAuthCallback) -> OAuthCallbackResult:
        """Resolve a completed OAuth ``callback`` to its linking outcome.

        Rejects — raising :class:`OAuthCallbackError` with
        :attr:`OAuthCallbackFailure.REJECTED` and changing no state — a callback
        that did not complete (denied, provider failure, incomplete) or whose
        ``state`` did not match the issued one (Req 10.5, 26.1, 26.6). A completed
        callback whose email is unverified at GitHub raises with
        :attr:`OAuthCallbackFailure.EMAIL_UNVERIFIED`, leaving any existing
        records unchanged (Req 10.3, 26.3).

        For a completed, verified callback: an existing ``oauth_link`` for the
        returned ``(provider, subject)`` signs in the linked User with no
        duplicate (Req 10.4); a verified email matching no User creates one User,
        records the ``oauth_link``, and establishes a Session (Req 10.1); a
        verified email matching an existing User stores a single-use pending
        confirmation and establishes a temporary, unauthenticated session (Req
        10.2, 10.7).
        """
        # Reject anything that is not a completed, state-matched callback before
        # touching any state. Denied/failed/incomplete and state-mismatch are
        # collapsed into one non-disclosing REJECTED reason; either way no User,
        # oauth_link, or Session is created and partial state is discarded (Req
        # 10.5, 26.1, 26.6).
        if not callback.completed or not callback.state_matches:
            raise OAuthCallbackError(OAuthCallbackFailure.REJECTED)

        # A GitHub-unverified email is never linked, and leaves an existing User's
        # records untouched (Req 10.3, 26.3). Checked before any lookup so the
        # refusal cannot have a side effect.
        if not callback.email_verified:
            raise OAuthCallbackError(OAuthCallbackFailure.EMAIL_UNVERIFIED)

        # A returning identity that already has a link signs in its User directly,
        # with no duplicate account (Req 10.4). This is checked before the email
        # match so a linked user signs in even if their GitHub email later changed.
        existing_link = self._oauth_links.get(callback.provider, callback.subject)
        if existing_link is not None:
            return self._sign_in(existing_link.user_stable_id)

        # No existing link: decide by the verified email. A matching existing User
        # requires explicit in-window confirmation (Req 10.2); no match creates a
        # fresh linked account (Req 10.1).
        existing_user = self._users.get_by_email(callback.email)
        if existing_user is not None:
            return self._begin_pending_confirmation(callback, existing_user)
        return self._create_and_sign_in(callback)

    def _create_and_sign_in(self, callback: OAuthCallback) -> OAuthCallbackResult:
        """Create one User, record its ``oauth_link``, and sign it in (Req 10.1).

        The User is created verified — GitHub has already verified the email, so
        the OAuth path yields a confirmed account directly (unlike self-service
        sign-up, which starts unverified). The durable ``oauth_link`` is recorded
        before the Session so a returning visitor resolves straight to the linked
        User (Req 10.4). Self-service paths assign only the learner role (Req
        13.3); role assignment is persisted by the repository adapter.
        """
        user = UserRecord(
            stable_id=self._stable_id_factory(),
            email=callback.email,
            locale=_DEFAULT_OAUTH_LOCALE,
            status=USER_STATUS_VERIFIED,
        )
        self._users.add(user)
        self._oauth_links.add(
            OAuthLink(
                provider=callback.provider,
                subject=callback.subject,
                user_stable_id=user.stable_id,
            )
        )
        return self._sign_in(user.stable_id)

    def _begin_pending_confirmation(
        self, callback: OAuthCallback, existing_user: UserRecord
    ) -> OAuthCallbackResult:
        """Store a single-use pending link and a temporary session (Req 10.2, 10.7).

        No ``oauth_link`` is created yet — only an explicit in-window confirmation
        creates it (Req 10.2, 26.2). The pending record's window is
        ``now + OAUTH_LINK_CONFIRMATION_WINDOW`` measured by the Clock (Req 26.5).
        A temporary session reference is minted so the visitor can carry it to the
        in-app confirmation step, but it is **unauthenticated** by construction: it
        is deliberately **never** written to the ``SessionStore`` (nor the durable
        ``SessionRepository``), so
        :meth:`~edu_it_application.authenticate.AuthenticateSessionUseCase.authenticate`
        resolves it to ``None`` and it grants no authenticated access to the
        existing User until the link is confirmed (Req 10.7). The only state this
        path creates is the single-use pending record; the confirm/decline step
        drives it from there.
        """
        issued_at = self._clock.now()
        pending_token = self._pending_token_factory()
        self._pending_links.add(
            PendingOAuthLink(
                token=pending_token,
                provider=callback.provider,
                subject=callback.subject,
                user_stable_id=existing_user.stable_id,
                email=callback.email,
                issued_at=issued_at,
                expires_at=issued_at + OAUTH_LINK_CONFIRMATION_WINDOW,
            )
        )
        temporary_session_ref = self._session_ref_factory()
        return OAuthCallbackResult(
            status=OAuthCallbackStatus.PENDING_CONFIRMATION,
            pending_token=pending_token,
            temporary_session_ref=temporary_session_ref,
        )

    def _sign_in(self, user_stable_id: str) -> OAuthCallbackResult:
        """Establish an authenticated Session for ``user_stable_id`` (Req 10.1, 10.4).

        Writes the durable ``session`` record first, then the ephemeral
        ``SessionStore`` state the request path resolves against (ADR 0004),
        expiring ``SESSION_DURATION`` after the current Clock instant, mirroring
        sign-in and reset.
        """
        server_side_ref = self._session_ref_factory()
        expires_at = self._clock.now() + SESSION_DURATION
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
        return OAuthCallbackResult(
            status=OAuthCallbackStatus.SIGNED_IN,
            server_side_ref=server_side_ref,
            user_stable_id=user_stable_id,
        )


# --- Confirmation / decline of a pending link (Req 10.2, 10.6, 26.2, 26.4, 26.5) ---


class OAuthLinkConfirmationOutcome(Enum):
    """The distinguishable outcomes of confirming a pending OAuth link.

    - :attr:`LINKED` — the pending record was unconsumed and inside the window;
      the ``oauth_link`` was made and an authenticated Session established (Req
      10.2, 26.2).
    - :attr:`EXPIRED_OR_INVALID` — the window had closed, the token was already
      consumed (confirmed or declined), or it was unrecognized; nothing is linked
      and the existing User's records are unchanged (Req 10.6, 26.4, 26.5). The
      distinct causes are collapsed into one outcome so the response does not
      disclose which occurred.
    """

    LINKED = "linked"
    """The link was confirmed within the window; a Session was established."""

    EXPIRED_OR_INVALID = "expired_or_invalid"
    """The confirmation window closed, or the token was consumed/unrecognized."""


@dataclass(frozen=True, slots=True)
class ConfirmOAuthLinkRequest:
    """A confirmation attempt handed to :meth:`ConfirmOAuthLinkUseCase.execute`.

    Carries only the opaque pending-confirmation token the temporary session
    holds (Req 10.7). The token alone resolves the pending record; an
    unrecognized token is rejected as expired/invalid without disclosing anything
    further.
    """

    token: str
    """The opaque single-use confirmation token."""


@dataclass(frozen=True, slots=True)
class OAuthLinkConfirmationResult:
    """The outcome of confirming a pending OAuth link (Req 10.2, 26.5).

    ``outcome`` is :attr:`OAuthLinkConfirmationOutcome.LINKED` only when a valid,
    unconsumed, in-window pending record was confirmed, the ``oauth_link`` made,
    and exactly one authenticated Session established; it then carries that
    Session's opaque ``server_side_ref`` and the linked user's ``stable_id``. On
    the :attr:`OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID` path nothing
    changed and both reference fields are ``None``.
    """

    outcome: OAuthLinkConfirmationOutcome
    """Whether the link was made or the confirmation was expired/invalid."""

    server_side_ref: str | None = None
    """The authenticated Session's opaque reference on success, else ``None``."""

    user_stable_id: str | None = None
    """The linked user's ``stable_id`` on success, else ``None``."""

    @property
    def is_linked(self) -> bool:
        """Whether the link was confirmed (the success path)."""
        return self.outcome is OAuthLinkConfirmationOutcome.LINKED


#: The single result every expired/consumed/unrecognized confirmation returns
#: (Req 10.6, 26.4, 26.5). All causes return this exact value, so the response
#: never discloses which cause occurred.
_CONFIRM_INVALID_RESULT = OAuthLinkConfirmationResult(
    outcome=OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
)


class ConfirmOAuthLinkUseCase:
    """Make the ``oauth_link`` and sign in, only on in-window confirmation (Req 10.2, 26.2)."""

    def __init__(
        self,
        *,
        pending_links: PendingOAuthLinkRepository,
        oauth_links: OAuthLinkRepository,
        sessions: SessionRepository,
        session_store: SessionStore,
        clock: Clock,
        session_ref_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``clock`` decides whether the pending record is still inside its window
        (Req 26.5) and sets the new Session's expiry at ``now + SESSION_DURATION``;
        ``session_ref_factory`` mints the authenticated Session's opaque reference.
        Both are injected so the flow is deterministic under test.
        """
        self._pending_links = pending_links
        self._oauth_links = oauth_links
        self._sessions = sessions
        self._session_store = session_store
        self._clock = clock
        self._session_ref_factory = session_ref_factory

    def execute(self, request: ConfirmOAuthLinkRequest) -> OAuthLinkConfirmationResult:
        """Confirm the pending link named by ``request`` and return the outcome.

        Returns :attr:`OAuthLinkConfirmationOutcome.LINKED` only when the token
        resolves to a pending record that is unconsumed and strictly inside its
        confirmation window as measured by the Clock: it records the
        ``oauth_link``, consumes the pending record (single use), and establishes
        exactly one authenticated Session (Req 10.2, 26.2). An unrecognized,
        expired, or already-consumed (confirmed or declined) token changes no
        state and returns the expired/invalid outcome, leaving the existing User's
        records unchanged (Req 10.6, 26.4, 26.5).
        """
        pending = self._pending_links.get(request.token)
        if pending is None or not pending.is_valid_at(self._clock.now()):
            return _CONFIRM_INVALID_RESULT

        # Consume the pending record: a successful mark_consumed is the single-use
        # gate. A False return means it was consumed between the lookup and here (a
        # concurrent confirm or a decline won the race), so this attempt is a second
        # use and must change nothing (Req 26.4).
        if not self._pending_links.mark_consumed(request.token, consumed_at=self._clock.now()):
            return _CONFIRM_INVALID_RESULT

        self._oauth_links.add(
            OAuthLink(
                provider=pending.provider,
                subject=pending.subject,
                user_stable_id=pending.user_stable_id,
            )
        )
        return self._sign_in(pending.user_stable_id)

    def _sign_in(self, user_stable_id: str) -> OAuthLinkConfirmationResult:
        """Establish an authenticated Session for the now-linked User (Req 10.2).

        Durable record first, then the ephemeral ``SessionStore`` state (ADR
        0004), expiring ``SESSION_DURATION`` after the current Clock instant,
        mirroring the sign-in path.
        """
        server_side_ref = self._session_ref_factory()
        expires_at = self._clock.now() + SESSION_DURATION
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
        return OAuthLinkConfirmationResult(
            outcome=OAuthLinkConfirmationOutcome.LINKED,
            server_side_ref=server_side_ref,
            user_stable_id=user_stable_id,
        )


@dataclass(frozen=True, slots=True)
class DeclineOAuthLinkRequest:
    """A decline handed to :meth:`DeclineOAuthLinkUseCase.execute`.

    Carries only the opaque pending-confirmation token. Declining an unrecognized
    or already-consumed token is a safe no-op.
    """

    token: str
    """The opaque single-use confirmation token being declined."""


@dataclass(frozen=True, slots=True)
class DeclineOAuthLinkResult:
    """The outcome of declining a pending OAuth link (Req 10.6, 26.4).

    The decline always succeeds (declining a stale token is a safe no-op), so
    this result is returned on every path. ``invalidated`` reports whether an
    unconsumed pending record was actually invalidated by this decline; it is an
    observational detail, not a success signal.
    """

    invalidated: bool
    """Whether an unconsumed pending record was invalidated by this decline."""


class DeclineOAuthLinkUseCase:
    """Invalidate a pending link immediately on decline; make no link (Req 10.6, 26.4)."""

    def __init__(
        self,
        *,
        pending_links: PendingOAuthLinkRepository,
        clock: Clock,
    ) -> None:
        """Wire the use case to the pending-link store and the Clock.

        ``clock`` stamps the pending record's consumption instant so the decline
        is recorded at the current time.
        """
        self._pending_links = pending_links
        self._clock = clock

    def execute(self, request: DeclineOAuthLinkRequest) -> DeclineOAuthLinkResult:
        """Invalidate the pending link named by ``request`` at once (Req 26.4).

        Marks the pending record consumed immediately so the token can never be
        confirmed later, and makes no ``oauth_link`` (Req 10.6, 26.4). Returns
        success on every path — an unrecognized or already-consumed token is a
        safe no-op — so a decline (or a repeated decline) is idempotent.
        ``invalidated`` reports whether this call invalidated an unconsumed record.
        """
        invalidated = self._pending_links.mark_consumed(
            request.token, consumed_at=self._clock.now()
        )
        return DeclineOAuthLinkResult(invalidated=invalidated)
