"""Sign-in, sign-out, and session authentication use cases (task 13.1, Req 8.1-8.5).

This module holds the three framework-free use cases that realize the design's
"Sign-in, sign-out, server-side sessions" behavior (design, Req 8), built on the
ports from prior tasks — the durable
:class:`~edu_it_domain.ports.repository.UserRepository` /
:class:`~edu_it_domain.ports.repository.SessionRepository`, the ephemeral
:class:`~edu_it_domain.ports.session_store.SessionStore` (ADR 0004), and the new
:class:`~edu_it_domain.ports.credential_store.CredentialStore`:

- :class:`AuthenticateUseCase` (sign-in) establishes a server-side Session
  **only** for valid credentials on a verified account, expiring
  ``SESSION_DURATION`` after creation as measured by the injected ``Clock``
  (Req 8.1). An unverified account, invalid credentials, or an unknown account
  establishes no Session and yields a non-disclosing failure (Req 8.3, 8.4).
  While the submitted identifier's failed attempts exceed the configured
  threshold within the configured window (the business constants in
  ``domain/config.py``, enforced by the injected
  :class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter`), the attempt is
  rejected **before** the credentials are evaluated, so no Session is
  established even for otherwise-valid credentials; once the window elapses per
  the injected ``Clock`` attempts are allowed again (Req 12.1, 12.2, 12.4).
- :class:`SignOutUseCase` revokes the current Session and succeeds even when no
  active Session is present, so sign-out is idempotent (Req 8.2).
- :class:`AuthenticateSessionUseCase` resolves a session reference presented by a
  request to its owning user **only while** the session is active (neither
  expired per the Clock nor revoked); an expired or revoked reference resolves to
  nothing and is never refreshed or reissued (Req 8.5, 28.1).

ADR 0004 shapes the session handling: the client holds only an opaque
``server_side_ref``; the authoritative ephemeral state lives in the
``SessionStore`` (Redis in production) and the durable ``session`` row in the
``SessionRepository`` (PostgreSQL) is the identity system of record. Sign-in
writes **both** — the durable record first, then the ephemeral state — so the
reference is re-verifiable server-side and survives in the system of record; the
``SessionStore`` is what each request resolves against. Revocation marks both.

Like the other account use cases this is deliberately framework-free: it imports
only the domain ports, records, and business constants, never a web or API
framework (Req 1.8). Every non-deterministic input — the current time and the
new session's opaque reference — enters through an injected collaborator, so the
whole flow is deterministic under test. Credential verification is delegated to
the ``CredentialStore`` (the raw password never becomes a stored secret here),
mirroring how sign-up injects its ``password_hasher``.

Non-disclosure is a property of the result type. Both sign-in failure causes an
``apps/api`` boundary may translate — an unverified account and invalid
credentials — are reported through :class:`SignInError` carrying a coarse
:class:`SignInFailure` reason, and the *invalid-credentials* reason is identical
whether the account is unknown or the password is wrong, so the response the
boundary renders for "wrong password" and "no such account" is the same (Req
8.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from edu_it_domain.config import SESSION_DURATION
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.rate_limiter import SignInRateLimiter
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    SessionRecord,
    SessionRepository,
    UserRepository,
)
from edu_it_domain.ports.session_store import SessionStore

__all__ = [
    "AuthenticateSessionUseCase",
    "AuthenticateUseCase",
    "AuthenticatedUser",
    "SignInError",
    "SignInFailure",
    "SignInRequest",
    "SignInResult",
    "SignOutRequest",
    "SignOutResult",
    "SignOutUseCase",
]


class SignInFailure(Enum):
    """Why a sign-in was refused, coarsely enough to stay non-disclosing (Req 8.3, 8.4).

    Three causes are distinguished, matching the translated Problem_Details the
    design calls for (Req 8.3, 8.4, 12.1):

    - :attr:`INVALID_CREDENTIALS` — the credentials do not match any verified
      account. This single reason covers an unknown account **and** a wrong
      password for a known account, so the boundary's response cannot disclose
      whether the account exists (Req 8.4).
    - :attr:`EMAIL_NOT_VERIFIED` — the credentials are valid for an account whose
      email is not verified (Req 8.3).
    - :attr:`RATE_LIMITED` — too many failed attempts for the submitted
      identifier within the configured window, so the attempt is rejected
      *without evaluating the credentials* and no Session is established even for
      valid credentials (Req 12.1, 12.4).

    An unverified account is reported separately from invalid credentials
    *by design*: Req 8.3 requires a distinct "email is not verified" message, and
    revealing that an account exists-but-unverified only after the correct
    password is supplied discloses nothing to someone who already proved they
    hold the credentials.
    """

    INVALID_CREDENTIALS = "invalid_credentials"
    """Credentials matched no verified account (unknown account or wrong password)."""

    EMAIL_NOT_VERIFIED = "email_not_verified"
    """Credentials were valid but the account's email is not verified (Req 8.3)."""

    RATE_LIMITED = "rate_limited"
    """Too many failed attempts; rejected before evaluating credentials (Req 12.1, 12.4)."""


class SignInError(Exception):
    """Raised when a sign-in is refused, carrying a non-disclosing reason (Req 8.3, 8.4).

    :attr:`failure` is the coarse :class:`SignInFailure` the ``apps/api`` boundary
    maps to a translated Problem_Details. The error is raised **before** any
    Session is created, so a refused sign-in establishes no Session (Req 8.3,
    8.4, 12.1). The invalid-credentials reason is identical for an unknown
    account and a wrong password, so the exception itself discloses nothing about
    account existence; the rate-limited reason is raised before the credentials
    are evaluated at all (Req 12.4).
    """

    def __init__(self, failure: SignInFailure) -> None:
        self.failure: SignInFailure = failure
        super().__init__(f"sign-in refused: {failure.value}")


@dataclass(frozen=True, slots=True)
class SignInRequest:
    """A sign-in submission handed to :meth:`AuthenticateUseCase.execute`.

    Both fields are the visitor's unvalidated input. The email identifies the
    account; the password is verified against the stored credential through the
    :class:`~edu_it_domain.ports.credential_store.CredentialStore`.
    """

    email: str
    """The submitted email address (the sign-in identifier)."""

    password: str
    """The submitted raw password (verified, never stored, by this use case)."""


@dataclass(frozen=True, slots=True)
class SignInResult:
    """The outcome of a successful sign-in (Req 8.1).

    Carries the opaque ``server_side_ref`` the client will hold — the only thing
    the client receives (ADR 0004) — and the ``stable_id`` of the authenticated
    user. A failed sign-in never produces this value; it raises
    :class:`SignInError` instead, so a result exists only when a Session was
    established.
    """

    server_side_ref: str
    """The opaque reference for the established Session (the only client-held token)."""

    user_stable_id: str
    """The ``stable_id`` of the user the Session authenticates."""


@dataclass(frozen=True, slots=True)
class SignOutRequest:
    """A sign-out request handed to :meth:`SignOutUseCase.execute`.

    ``server_side_ref`` is the reference of the Session to revoke. It may name no
    active Session (unknown, already revoked, or already expired, or ``None`` when
    the caller presented nothing); sign-out succeeds regardless (Req 8.2).
    """

    server_side_ref: str | None
    """The reference of the Session to revoke, or ``None`` when none is present."""


@dataclass(frozen=True, slots=True)
class SignOutResult:
    """The outcome of a sign-out (Req 8.2).

    Sign-out always succeeds, so this result is returned on every path. ``revoked``
    reports whether an active Session was actually invalidated (``True``) or there
    was nothing to revoke (``False``); it is an observational detail, not a
    success signal — the operation is successful either way, which is what makes
    sign-out idempotent (Req 8.2).
    """

    revoked: bool
    """Whether an active Session was invalidated; sign-out succeeds either way."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The user a presented, still-active Session authenticates (Req 8.5).

    Returned by :meth:`AuthenticateSessionUseCase.authenticate` only while the
    Session is active. It names the owning user so the caller re-verifies
    ownership server-side (ADR 0004) rather than trusting a client-held claim.
    """

    user_stable_id: str
    """The ``stable_id`` of the user the active Session authenticates."""

    server_side_ref: str
    """The opaque reference that was resolved to this user."""


class AuthenticateUseCase:
    """Establish a server-side Session for valid, verified credentials (Req 8.1)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        credentials: CredentialStore,
        sessions: SessionRepository,
        session_store: SessionStore,
        rate_limiter: SignInRateLimiter,
        clock: Clock,
        session_ref_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``session_ref_factory`` mints the new Session's opaque ``server_side_ref``;
        it is injected so the flow is deterministic under test and no secret
        generation leaks into the domain. ``clock`` sets the Session's expiry at
        ``now + SESSION_DURATION`` (Req 8.1). The durable record and the ephemeral
        state are written through ``sessions`` and ``session_store`` respectively
        (ADR 0004); credential verification is delegated to ``credentials``.

        ``rate_limiter`` throttles repeated failed sign-ins per identifier: it is
        consulted *before* the credentials are evaluated and records each failed
        attempt, enforcing the configured threshold and window from
        ``domain/config.py`` against the injected ``Clock`` (Req 12.1, 12.2,
        12.4).
        """
        self._users = users
        self._credentials = credentials
        self._sessions = sessions
        self._session_store = session_store
        self._rate_limiter = rate_limiter
        self._clock = clock
        self._session_ref_factory = session_ref_factory

    def execute(self, request: SignInRequest) -> SignInResult:
        """Authenticate the submission and establish a Session, or refuse it.

        Establishes a server-side Session expiring ``SESSION_DURATION`` after the
        current Clock instant **only** when the email maps to a verified account
        whose stored credential matches the submitted password (Req 8.1). An
        unknown account or a wrong password raises :class:`SignInError` with
        :attr:`SignInFailure.INVALID_CREDENTIALS` (non-disclosing, Req 8.4); a
        valid credential for an unverified account raises it with
        :attr:`SignInFailure.EMAIL_NOT_VERIFIED` (Req 8.3). No Session is created
        on any refusal path.

        Rate limiting runs *first* (Req 12.1, 12.4): while the submitted
        identifier's failed attempts exceed the configured threshold within the
        window, the attempt is rejected with
        :attr:`SignInFailure.RATE_LIMITED` **before** the credentials are
        evaluated, so no Session is established even for valid credentials. Each
        refused credential attempt is recorded as a failure, and a successful
        sign-in clears the identifier's counter so a legitimate user is not
        penalised by earlier failures; the window elapses per the injected Clock
        (Req 12.2).
        """
        identifier = request.email
        # Rate-limit check short-circuits before any credential evaluation: while
        # the identifier is over the threshold we refuse without touching the
        # credentials or establishing a Session, even for valid ones (Req 12.1,
        # 12.4). The limiter reopens the identifier once the window elapses per
        # the injected Clock (Req 12.2).
        if self._rate_limiter.is_blocked(identifier):
            raise SignInError(SignInFailure.RATE_LIMITED)

        user = self._users.get_by_email(request.email)
        # Unknown account and wrong password are collapsed into one reason so the
        # refusal cannot disclose whether the account exists (Req 8.4). The
        # credential check runs only for a known user; for an unknown email we
        # report the same invalid-credentials failure without any lookup result.
        if user is None or not self._credentials.verify_password(user.stable_id, request.password):
            # A failed attempt counts toward the identifier's rate limit (Req 12.1).
            self._rate_limiter.register_failure(identifier)
            raise SignInError(SignInFailure.INVALID_CREDENTIALS)

        # Credentials are valid; only now is an unverified account distinguishable
        # (Req 8.3). Revealing the unverified state here discloses nothing to a
        # party that has already proven it holds the correct password.
        if user.status != USER_STATUS_VERIFIED:
            # An unverified account is still a failed sign-in for the identifier.
            self._rate_limiter.register_failure(identifier)
            raise SignInError(SignInFailure.EMAIL_NOT_VERIFIED)

        # A successful sign-in clears the counter so earlier failures do not carry
        # forward (the limiter's reset path).
        self._rate_limiter.reset(identifier)
        return self._establish_session(user.stable_id)

    def _establish_session(self, user_stable_id: str) -> SignInResult:
        """Create the durable record then the ephemeral state for a new Session.

        Expiry is ``now + SESSION_DURATION`` measured by the injected Clock
        (Req 8.1). The durable ``session`` record (the identity system of record,
        ADR 0004) is written first, then the ephemeral ``SessionStore`` state the
        request path resolves against, so the reference is both persisted and
        immediately resolvable.
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
        return SignInResult(server_side_ref=server_side_ref, user_stable_id=user_stable_id)


class SignOutUseCase:
    """Revoke the current Session; succeed even when none is present (Req 8.2)."""

    def __init__(
        self,
        *,
        sessions: SessionRepository,
        session_store: SessionStore,
        clock: Clock,
    ) -> None:
        """Wire the use case to its collaborators.

        Both the durable record (``sessions``) and the ephemeral state
        (``session_store``) are revoked so a signed-out reference can no longer
        authenticate through either (ADR 0004). ``clock`` stamps the durable
        record's revocation instant.
        """
        self._sessions = sessions
        self._session_store = session_store
        self._clock = clock

    def execute(self, request: SignOutRequest) -> SignOutResult:
        """Revoke the presented Session and return success (Req 8.2).

        Revokes both the ephemeral state and the durable record for the presented
        reference. Returns success on every path — including when no reference is
        presented or the reference names no active Session — so repeating sign-out
        is a safe no-op (idempotent, Req 8.2). ``revoked`` reports whether an
        active Session was actually invalidated.
        """
        server_side_ref = request.server_side_ref
        if server_side_ref is None:
            return SignOutResult(revoked=False)

        revoked = self._session_store.revoke(server_side_ref)
        # Keep the durable system-of-record in step with the ephemeral store
        # (ADR 0004). The durable revoke is idempotent too, so a reference already
        # revoked (or unknown) stays a safe no-op and sign-out still succeeds.
        self._sessions.revoke(server_side_ref, revoked_at=self._clock.now())
        return SignOutResult(revoked=revoked)


class AuthenticateSessionUseCase:
    """Authenticate a request by its session reference, only while active (Req 8.5)."""

    def __init__(self, *, session_store: SessionStore) -> None:
        """Wire the use case to the ephemeral ``SessionStore`` (ADR 0004).

        A request is authenticated by resolving the reference it presents against
        the ``SessionStore``, which the request path reads on every call; the
        store decides activeness against the injected Clock, so an expired or
        revoked reference resolves to nothing without any extra check here.
        """
        self._session_store = session_store

    def authenticate(self, server_side_ref: str | None) -> AuthenticatedUser | None:
        """Resolve ``server_side_ref`` to its user, or ``None`` when inactive.

        Returns the :class:`AuthenticatedUser` the Session owns **only while** the
        Session is active — neither expired (per the Clock) nor revoked. An absent
        reference, an unknown reference, or one whose Session is expired or revoked
        resolves to ``None``, so the request is treated as unauthenticated and the
        Session is never refreshed or reissued (Req 8.5, 28.1).
        """
        if server_side_ref is None:
            return None
        resolved = self._session_store.resolve(server_side_ref)
        if resolved is None:
            return None
        return AuthenticatedUser(
            user_stable_id=resolved.user_stable_id,
            server_side_ref=resolved.server_side_ref,
        )
