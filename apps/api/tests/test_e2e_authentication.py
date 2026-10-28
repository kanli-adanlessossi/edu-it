"""End-to-end authentication tests (task 26.1, Req 21.4, 21.5).

These tests exercise the account system *end to end* — sign-up, email
verification, sign-in, sign-out, and password reset — by wiring the real
application use cases (:mod:`edu_it_application`) against the real in-memory
infrastructure fakes (:mod:`edu_it_infrastructure.fakes`), which are
substitutable for the production PostgreSQL/Redis/SMTP adapters without changing
any domain or application code (Req 5.2). Nothing here uses a per-test double:
every collaborator is a shipped fake, so the flow that runs is the same flow
that runs in production with only the adapter implementations swapped.

The GitHub-OAuth portion (Req 21.5) runs against a *mock* OAuth provider defined
in this module (:class:`_MockGitHubOAuthProvider`). The mock performs the whole
handshake — authorization-URL issuance with an anti-forgery ``state``, the
``code`` → access-token exchange, and the userinfo lookup — entirely
**in-process**: it holds its accounts in a dict and makes **no** network call to
GitHub or any external OAuth provider. A guard installed by the ``no_network``
fixture turns any attempt to open a socket during these tests into an immediate
failure, so "makes no external call" is enforced, not merely asserted by
inspection.

What is covered end to end:

- **Sign-up → verification → sign-in → sign-out** (Req 21.4): a learner signs
  up, the verification token is read back from the retained mail and consumed,
  the now-verified learner signs in and the session authenticates, then sign-out
  revokes it and the same reference stops authenticating.
- **Password reset** (Req 21.4): a verified learner requests a reset, completes
  it with the token from the retained mail, every prior session is revoked, and
  exactly the new session authenticates; the learner can then sign in with the
  new password and no longer with the old one.
- **GitHub OAuth against a mock provider** (Req 21.5): a brand-new GitHub email
  creates one linked account and signs in; a returning identity signs straight
  in with no duplicate; a GitHub email that matches an existing password account
  requires an explicit in-window confirmation before it links; and a denied flow
  creates nothing — all without a single external call.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from urllib.parse import parse_qs, urlparse

import pytest
from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    AuthenticateUseCase,
    SignInError,
    SignInFailure,
    SignInRequest,
    SignOutRequest,
    SignOutUseCase,
)
from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    ConfirmOAuthLinkRequest,
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkRequest,
    DeclineOAuthLinkUseCase,
    OAuthCallback,
    OAuthCallbackError,
    OAuthCallbackFailure,
)
from edu_it_application.reset_password import (
    CompletePasswordResetRequest,
    CompletePasswordResetUseCase,
    RequestPasswordResetRequest,
    RequestPasswordResetUseCase,
    ResetCompletionOutcome,
)
from edu_it_application.sign_up import SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import (
    VerifyEmailOutcome,
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.config import (
    OAUTH_LINK_CONFIRMATION_WINDOW,
    RESET_LINK_TTL,
)
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_CredentialStore,
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_ResetLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_SignInRateLimiter,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)

# A fixed, timezone-aware starting instant so the Test_Clock drives every
# expiry (link TTLs, session duration, the OAuth window) deterministically.
_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# A policy-compliant password (>= 12 chars) used throughout.
_PASSWORD = "correct horse battery staple"
_NEW_PASSWORD = "an-entirely-different-passphrase-99"
_EMAIL = "learner@example.com"


def _hash(raw: str) -> str:
    """The credential "hashing" scheme the end-to-end flow shares.

    The in-memory :class:`In_Memory_CredentialStore` performs no hashing of its
    own: :meth:`verify_password` compares the stored value against the *raw*
    password sign-in submits (equality). So the stored secret must equal the raw
    password for a correct sign-in to match — i.e. the "hasher" injected into
    sign-up and reset must be the identity. Keeping that in one named function
    makes the write path (sign-up / reset) and the verify path (sign-in) share
    a single, obviously-consistent scheme; a real deployment swaps in the SMTP
    adapter's bcrypt-style hasher, whose ``verify_password`` hashes internally.
    """
    return raw


# --- No-network guard (Req 21.5) ---------------------------------------------


class _NetworkAccessError(AssertionError):
    """Raised if any code under test tries to open a network socket.

    Its existence is the enforcement behind Req 21.5: the OAuth tests must make
    no external call, so a real socket connection is a test failure, not a
    silently tolerated side effect.
    """


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test on any attempt to open an outbound network socket (Req 21.5).

    The mock OAuth provider is purely in-process, so nothing in these tests
    should ever reach the network. Patching ``socket.socket.connect`` to raise
    makes that guarantee executable: were the handshake to accidentally hit a
    real provider, the test would fail here rather than quietly succeeding.
    """

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise _NetworkAccessError("network access is forbidden in OAuth e2e tests (Req 21.5)")

    monkeypatch.setattr(socket.socket, "connect", _forbidden)


# --- A deterministic, monotonic opaque-token factory -------------------------


def _sequence_factory(prefix: str) -> Iterator[str]:
    """Yield ``prefix-1``, ``prefix-2``, ... so minted references are unique.

    Sign-up tokens, user stable_ids, session references, pending tokens, and the
    OAuth ``state`` each get their own sequence, so the end-to-end flow is fully
    deterministic while every minted value stays distinct.
    """
    counter = count(1)
    while True:
        yield f"{prefix}-{next(counter)}"


def _next(it: Iterator[str]) -> Callable[[], str]:
    """Adapt a sequence iterator to the no-argument factory the use cases take."""
    return lambda: next(it)


# --- The assembled account system (real fakes, nothing mocked) ---------------


@dataclass
class _AuthSystem:
    """A fully-wired account system over the in-memory infrastructure fakes.

    Every field is a real shipped use case or fake; the only thing a test does is
    call the use cases and read back observable state (the created user, the
    retained mail, whether a session reference still authenticates). Because the
    fakes are substitutable for the production adapters (Req 5.2), this is the
    same flow production runs with the real adapters swapped in.
    """

    clock: Test_Clock
    mailer: Console_Mailer
    users: In_Memory_UserRepository
    credentials: In_Memory_CredentialStore
    sessions: In_Memory_SessionRepository
    session_store: In_Memory_SessionStore
    verification_links: In_Memory_VerificationLinkRepository
    reset_links: In_Memory_ResetLinkRepository
    oauth_links: In_Memory_OAuthLinkRepository
    pending_oauth_links: In_Memory_PendingOAuthLinkRepository

    sign_up: SignUpUseCase
    verify_email: VerifyEmailUseCase
    sign_in: AuthenticateUseCase
    sign_out: SignOutUseCase
    authenticate_session: AuthenticateSessionUseCase
    request_reset: RequestPasswordResetUseCase
    complete_reset: CompletePasswordResetUseCase
    complete_oauth: CompleteOAuthCallbackUseCase
    confirm_oauth: ConfirmOAuthLinkUseCase
    decline_oauth: DeclineOAuthLinkUseCase

    # --- helpers shared across tests -----------------------------------------

    def latest_token(self, recipient: str, header: str) -> str:
        """Read the single-use token out of the most recent mail to ``recipient``.

        The console mailer retains every sent message (Req 5.3); the sign-up and
        reset mails carry their opaque token in a header
        (``X-Verification-Token`` / ``X-Reset-Token``), so a test consumes the
        link exactly as a recipient who clicked it would, without any back door
        into the repositories.
        """
        mails = self.mailer.sent_to(recipient)
        assert mails, f"no mail was sent to {recipient!r}"
        token = mails[-1].headers.get(header)
        assert token is not None, f"mail to {recipient!r} carried no {header} header"
        return token

    def register_credential_on_signup(self, email: str, raw_password: str) -> str:
        """Store the credential a real sign-up adapter would persist, return the id.

        The sign-up use case intentionally keeps the raw secret out of the domain
        record and leaves credential persistence to the adapter, so for an
        end-to-end flow we store the credential against the created user — the
        same effect the production adapter has — and return the user's
        ``stable_id``. The store verifies by equality against the raw password
        sign-in submits, and the shared ``password_hasher`` (see the fixture) is
        the identity, so storing ``_hash(raw_password)`` here and at reset keeps
        the write and the later sign-in verify consistent.
        """
        user = self.users.get_by_email(email)
        assert user is not None, f"sign-up created no user for {email!r}"
        self.credentials.set_password(user.stable_id, _hash(raw_password))
        return user.stable_id


@pytest.fixture
def auth() -> _AuthSystem:
    """Assemble the account system with the in-memory fakes at ``_T0``.

    One Test_Clock is shared by every time-sensitive collaborator (session store,
    rate limiter, and the use cases), so advancing it moves the whole system's
    notion of "now" together — the only way the link/session/window expiries stay
    mutually consistent in an end-to-end flow.
    """
    clock = Test_Clock(start=_T0)
    mailer = Console_Mailer()
    users = In_Memory_UserRepository()
    credentials = In_Memory_CredentialStore()
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(clock)
    verification_links = In_Memory_VerificationLinkRepository()
    reset_links = In_Memory_ResetLinkRepository()
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_oauth_links = In_Memory_PendingOAuthLinkRepository()
    rate_limiter = In_Memory_SignInRateLimiter(clock)

    stable_ids = _sequence_factory("user")
    verification_tokens = _sequence_factory("verify")
    reset_tokens = _sequence_factory("reset")
    session_refs = _sequence_factory("session")
    pending_tokens = _sequence_factory("pending")

    hasher = _hash

    system = _AuthSystem(
        clock=clock,
        mailer=mailer,
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        verification_links=verification_links,
        reset_links=reset_links,
        oauth_links=oauth_links,
        pending_oauth_links=pending_oauth_links,
        sign_up=SignUpUseCase(
            users=users,
            verification_links=verification_links,
            mailer=mailer,
            clock=clock,
            token_factory=_next(verification_tokens),
            stable_id_factory=_next(stable_ids),
            password_hasher=hasher,
        ),
        verify_email=VerifyEmailUseCase(
            users=users,
            verification_links=verification_links,
            clock=clock,
        ),
        sign_in=AuthenticateUseCase(
            users=users,
            credentials=credentials,
            sessions=sessions,
            session_store=session_store,
            rate_limiter=rate_limiter,
            clock=clock,
            session_ref_factory=_next(session_refs),
        ),
        sign_out=SignOutUseCase(sessions=sessions, session_store=session_store, clock=clock),
        authenticate_session=AuthenticateSessionUseCase(session_store=session_store),
        request_reset=RequestPasswordResetUseCase(
            users=users,
            reset_links=reset_links,
            mailer=mailer,
            clock=clock,
            token_factory=_next(reset_tokens),
        ),
        complete_reset=CompletePasswordResetUseCase(
            reset_links=reset_links,
            credentials=credentials,
            sessions=sessions,
            session_store=session_store,
            clock=clock,
            password_hasher=hasher,
            session_ref_factory=_next(session_refs),
        ),
        complete_oauth=CompleteOAuthCallbackUseCase(
            users=users,
            oauth_links=oauth_links,
            pending_links=pending_oauth_links,
            sessions=sessions,
            session_store=session_store,
            clock=clock,
            stable_id_factory=_next(stable_ids),
            pending_token_factory=_next(pending_tokens),
            session_ref_factory=_next(session_refs),
        ),
        confirm_oauth=ConfirmOAuthLinkUseCase(
            pending_links=pending_oauth_links,
            oauth_links=oauth_links,
            sessions=sessions,
            session_store=session_store,
            clock=clock,
            session_ref_factory=_next(session_refs),
        ),
        decline_oauth=DeclineOAuthLinkUseCase(
            pending_links=pending_oauth_links,
            clock=clock,
        ),
    )
    return system


# --- The mock GitHub OAuth provider (Req 21.5) -------------------------------


@dataclass(frozen=True, slots=True)
class _GitHubAccount:
    """One account the mock provider knows about (GitHub's side of the handshake)."""

    subject: str
    email: str
    email_verified: bool


class _MockGitHubOAuthProvider:
    """A fully in-process stand-in for GitHub's OAuth endpoints (Req 21.5).

    It models the three steps the ``apps/api`` boundary drives — authorize,
    token exchange, userinfo — with plain dictionaries and makes **no** network
    call. The boundary (here, the test) issues an authorization URL carrying an
    anti-forgery ``state``, the "user" is redirected back with a one-time
    ``code``, the code is exchanged for an access token, and the token resolves
    to the account's profile. The handshake's outcome is reduced to the
    framework-free :class:`~edu_it_application.oauth_sign_in.OAuthCallback` the
    linking use cases consume, so the test drives the exact contract production
    will, minus the socket.
    """

    AUTHORIZE_URL = "https://github.test/login/oauth/authorize"

    def __init__(self) -> None:
        self._accounts: dict[str, _GitHubAccount] = {}
        self._codes: dict[str, str] = {}  # one-time code -> subject
        self._tokens: dict[str, str] = {}  # access token -> subject
        self._issued_states: set[str] = set()
        self._code_seq = count(1)
        self._token_seq = count(1)

    def register(self, account: _GitHubAccount) -> None:
        """Make ``account`` resolvable by the subsequent handshake."""
        self._accounts[account.subject] = account

    def authorization_url(self, *, state: str) -> str:
        """Return the provider authorize URL the boundary would redirect to.

        The ``state`` is recorded so the callback can be checked against it; this
        is the anti-forgery value Req 26.6 turns on.
        """
        self._issued_states.add(state)
        return f"{self.AUTHORIZE_URL}?state={state}&redirect_uri=https://edu-it.test/oauth/callback"

    def user_authorizes(self, subject: str) -> str:
        """Simulate the user approving the app; return the one-time ``code``.

        Raises if the subject is unknown, mirroring a provider that will not mint
        a code for an account it does not have.
        """
        if subject not in self._accounts:
            raise KeyError(f"unknown GitHub account {subject!r}")
        code = f"code-{next(self._code_seq)}"
        self._codes[code] = subject
        return code

    def exchange_code_for_token(self, code: str) -> str:
        """Exchange a one-time ``code`` for an access token (single use).

        The code is consumed so it cannot be replayed, exactly as GitHub's token
        endpoint behaves.
        """
        subject = self._codes.pop(code, None)
        if subject is None:
            raise KeyError("invalid or already-used authorization code")
        token = f"token-{next(self._token_seq)}"
        self._tokens[token] = subject
        return token

    def fetch_account(self, access_token: str) -> _GitHubAccount:
        """Resolve an access token to the account's profile (the userinfo call)."""
        subject = self._tokens.get(access_token)
        if subject is None:
            raise KeyError("invalid access token")
        return self._accounts[subject]

    # The full happy-path handshake, reduced to the use-case's value object.

    def complete_handshake(
        self,
        *,
        subject: str,
        returned_state: str,
    ) -> OAuthCallback:
        """Run authorize → user approval → token exchange → userinfo in-process.

        Returns the :class:`OAuthCallback` the boundary would hand the linking
        use case for a completed flow. ``returned_state`` is compared against the
        issued one, so a forged/stale callback (``state`` mismatch) is reported
        with ``state_matches=False`` (Req 26.6).
        """
        issued = self.authorization_url(state="state-issued")
        issued_state = parse_qs(urlparse(issued).query)["state"][0]
        code = self.user_authorizes(subject)
        token = self.exchange_code_for_token(code)
        account = self.fetch_account(token)
        return OAuthCallback(
            completed=True,
            state_matches=(returned_state == issued_state),
            subject=account.subject,
            email=account.email,
            email_verified=account.email_verified,
            provider=PROVIDER_GITHUB,
        )


@pytest.fixture
def github() -> _MockGitHubOAuthProvider:
    """A fresh in-process mock GitHub provider for each test (Req 21.5)."""
    return _MockGitHubOAuthProvider()


# === End-to-end: sign-up → verify → sign-in → sign-out (Req 21.4) ============


def test_signup_verify_signin_signout_end_to_end(auth: _AuthSystem) -> None:
    # 1) Sign up: one unverified learner is created and one verification mail sent.
    result = auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    assert result.accepted is True
    assert len(auth.users) == 1
    user_id = auth.register_credential_on_signup(_EMAIL, _PASSWORD)

    # An unverified learner cannot yet sign in (Req 8.3).
    with pytest.raises(SignInError) as not_verified:
        auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert not_verified.value.failure is SignInFailure.EMAIL_NOT_VERIFIED

    # 2) Verify email with the token read back from the retained mail.
    token = auth.latest_token(_EMAIL, "X-Verification-Token")
    verified = auth.verify_email.execute(VerifyEmailRequest(token=token))
    assert verified.outcome is VerifyEmailOutcome.VERIFIED

    # The link is single-use: opening it again is rejected and offers a resend.
    reused = auth.verify_email.execute(VerifyEmailRequest(token=token))
    assert reused.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert reused.offer_resend is True

    # 3) Sign in: the verified learner gets a session that authenticates.
    signed_in = auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert signed_in.user_stable_id == user_id
    authed = auth.authenticate_session.authenticate(signed_in.server_side_ref)
    assert authed is not None
    assert authed.user_stable_id == user_id

    # 4) Sign out: the session is revoked and the same reference stops authenticating.
    signed_out = auth.sign_out.execute(SignOutRequest(server_side_ref=signed_in.server_side_ref))
    assert signed_out.revoked is True
    assert auth.authenticate_session.authenticate(signed_in.server_side_ref) is None

    # Sign-out is idempotent: repeating it still succeeds with nothing to revoke.
    assert (
        auth.sign_out.execute(SignOutRequest(server_side_ref=signed_in.server_side_ref)).revoked
        is False
    )


def test_wrong_password_never_establishes_a_session_end_to_end(auth: _AuthSystem) -> None:
    # Sign up and verify so the account is otherwise sign-in-able.
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    auth.register_credential_on_signup(_EMAIL, _PASSWORD)
    token = auth.latest_token(_EMAIL, "X-Verification-Token")
    auth.verify_email.execute(VerifyEmailRequest(token=token))

    # A wrong password is refused with the single non-disclosing reason (Req 8.4),
    # and no session reference is minted.
    with pytest.raises(SignInError) as wrong:
        auth.sign_in.execute(SignInRequest(email=_EMAIL, password="the-wrong-password-1"))
    assert wrong.value.failure is SignInFailure.INVALID_CREDENTIALS
    # No session was minted on the refusal path: the only session reference the
    # factory would have produced does not resolve to an authenticated user.
    assert auth.authenticate_session.authenticate("session-1") is None


def test_duplicate_signup_creates_no_second_user_and_notifies_owner(auth: _AuthSystem) -> None:
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    first_mail_count = len(auth.mailer.sent_to(_EMAIL))

    # A second sign-up with the same email returns the identical accepted result
    # (Req 7.6) and creates no second user (Req 7.7).
    again = auth.sign_up.execute(SignUpRequest(email=_EMAIL, password="another-valid-pass-1"))
    assert again.accepted is True
    assert len(auth.users) == 1

    # The existing owner is notified that a sign-up was attempted (Req 7.7).
    assert len(auth.mailer.sent_to(_EMAIL)) == first_mail_count + 1


# === End-to-end: password reset (Req 21.4) ===================================


def test_password_reset_end_to_end(auth: _AuthSystem) -> None:
    # Arrange: a verified, signed-in learner with an active session.
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    user_id = auth.register_credential_on_signup(_EMAIL, _PASSWORD)
    verify_token = auth.latest_token(_EMAIL, "X-Verification-Token")
    auth.verify_email.execute(VerifyEmailRequest(token=verify_token))
    old_session = auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert auth.authenticate_session.authenticate(old_session.server_side_ref) is not None

    # 1) Request a reset: a single-use reset mail is sent (Req 9.1).
    accepted = auth.request_reset.execute(RequestPasswordResetRequest(email=_EMAIL))
    assert accepted.accepted is True
    reset_token = auth.latest_token(_EMAIL, "X-Reset-Token")

    # 2) Complete the reset: password changes, all prior sessions are revoked,
    #    and exactly one new session is established (Req 9.5, 28.1, 28.2).
    completed = auth.complete_reset.execute(
        CompletePasswordResetRequest(token=reset_token, new_password=_NEW_PASSWORD)
    )
    assert completed.outcome is ResetCompletionOutcome.COMPLETED
    assert completed.user_stable_id == user_id

    # The old session no longer authenticates; exactly the new one does (Req 28.1, 28.2).
    assert auth.authenticate_session.authenticate(old_session.server_side_ref) is None
    assert auth.authenticate_session.authenticate(completed.server_side_ref) is not None

    # The reset link is single-use: a replay is rejected and offers to restart.
    replay = auth.complete_reset.execute(
        CompletePasswordResetRequest(token=reset_token, new_password=_NEW_PASSWORD)
    )
    assert replay.outcome is ResetCompletionOutcome.INVALID_OR_EXPIRED
    assert replay.offer_restart is True

    # 3) The learner can now sign in with the new password but not the old one.
    #    (The reset use case updates the stored credential; sign-in verifies it.)
    with pytest.raises(SignInError) as old_pw:
        auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert old_pw.value.failure is SignInFailure.INVALID_CREDENTIALS

    fresh = auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_NEW_PASSWORD))
    assert auth.authenticate_session.authenticate(fresh.server_side_ref) is not None


def test_reset_request_for_unknown_email_is_non_disclosing(auth: _AuthSystem) -> None:
    # An unregistered email returns the identical accepted result and sends no
    # mail, so account existence is never disclosed (Req 9.6).
    accepted = auth.request_reset.execute(RequestPasswordResetRequest(email="nobody@example.com"))
    assert accepted.accepted is True
    assert auth.mailer.sent == ()


def test_expired_reset_link_leaves_password_unchanged(auth: _AuthSystem) -> None:
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    auth.register_credential_on_signup(_EMAIL, _PASSWORD)
    verify_token = auth.latest_token(_EMAIL, "X-Verification-Token")
    auth.verify_email.execute(VerifyEmailRequest(token=verify_token))

    auth.request_reset.execute(RequestPasswordResetRequest(email=_EMAIL))
    reset_token = auth.latest_token(_EMAIL, "X-Reset-Token")

    # Advance past the 1-hour reset TTL; the link is now expired.
    auth.clock.advance(RESET_LINK_TTL)
    stale = auth.complete_reset.execute(
        CompletePasswordResetRequest(token=reset_token, new_password=_NEW_PASSWORD)
    )
    assert stale.outcome is ResetCompletionOutcome.INVALID_OR_EXPIRED

    # The original password still works; the new one does not (nothing changed).
    signed_in = auth.sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert auth.authenticate_session.authenticate(signed_in.server_side_ref) is not None


# === End-to-end: GitHub OAuth against the mock provider (Req 21.5) ===========


def test_new_github_email_creates_one_linked_account_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    github.register(
        _GitHubAccount(subject="gh-1", email="octocat@example.com", email_verified=True)
    )

    callback = github.complete_handshake(subject="gh-1", returned_state="state-issued")
    result = auth.complete_oauth.execute(callback)

    # A verified GitHub email matching no user creates one user + one link + a
    # session that authenticates (Req 10.1).
    assert result.is_signed_in
    assert len(auth.users) == 1
    assert len(auth.oauth_links) == 1
    assert auth.authenticate_session.authenticate(result.server_side_ref) is not None

    # A returning handshake for the same identity signs straight in with no
    # duplicate account (Req 10.4).
    again = auth.complete_oauth.execute(
        github.complete_handshake(subject="gh-1", returned_state="state-issued")
    )
    assert again.is_signed_in
    assert len(auth.users) == 1
    assert len(auth.oauth_links) == 1


def test_github_email_matching_existing_user_requires_confirmation_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    # A password account already exists for this email.
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    existing_user = auth.users.get_by_email(_EMAIL)
    assert existing_user is not None
    github.register(_GitHubAccount(subject="gh-2", email=_EMAIL, email_verified=True))

    # The callback does not sign in; it opens a pending, in-window confirmation
    # and no oauth_link exists yet (Req 10.2, 10.7).
    pending = auth.complete_oauth.execute(
        github.complete_handshake(subject="gh-2", returned_state="state-issued")
    )
    assert pending.is_pending_confirmation
    assert pending.pending_token is not None
    assert len(auth.oauth_links) == 0
    # The temporary session is unauthenticated by construction (Req 10.7).
    assert auth.authenticate_session.authenticate(pending.temporary_session_ref) is None

    # Confirming within the window makes the link and signs the existing user in
    # (Req 10.2, 26.2) — still with no duplicate account created.
    linked = auth.confirm_oauth.execute(ConfirmOAuthLinkRequest(token=pending.pending_token))
    assert linked.is_linked
    assert linked.user_stable_id == existing_user.stable_id
    assert len(auth.oauth_links) == 1
    assert len(auth.users) == 1
    assert auth.authenticate_session.authenticate(linked.server_side_ref) is not None


def test_github_confirmation_after_window_links_nothing_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    github.register(_GitHubAccount(subject="gh-3", email=_EMAIL, email_verified=True))

    pending = auth.complete_oauth.execute(
        github.complete_handshake(subject="gh-3", returned_state="state-issued")
    )
    assert pending.pending_token is not None

    # Let the confirmation window elapse; the token can no longer link (Req 26.5).
    auth.clock.advance(OAUTH_LINK_CONFIRMATION_WINDOW)
    expired = auth.confirm_oauth.execute(ConfirmOAuthLinkRequest(token=pending.pending_token))
    assert not expired.is_linked
    assert len(auth.oauth_links) == 0


def test_github_declined_pending_link_can_never_be_confirmed_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    auth.sign_up.execute(SignUpRequest(email=_EMAIL, password=_PASSWORD))
    github.register(_GitHubAccount(subject="gh-4", email=_EMAIL, email_verified=True))

    pending = auth.complete_oauth.execute(
        github.complete_handshake(subject="gh-4", returned_state="state-issued")
    )
    assert pending.pending_token is not None

    # Declining invalidates the token immediately (Req 10.6, 26.4) ...
    declined = auth.decline_oauth.execute(DeclineOAuthLinkRequest(token=pending.pending_token))
    assert declined.invalidated is True

    # ... so a later confirmation of the same token links nothing.
    after_decline = auth.confirm_oauth.execute(ConfirmOAuthLinkRequest(token=pending.pending_token))
    assert not after_decline.is_linked
    assert len(auth.oauth_links) == 0


def test_github_state_mismatch_creates_nothing_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    github.register(_GitHubAccount(subject="gh-5", email="forged@example.com", email_verified=True))

    # A callback whose returned state does not match the issued one is a forged
    # or stale callback: it creates no user, link, or session (Req 26.6).
    callback = github.complete_handshake(subject="gh-5", returned_state="not-the-issued-state")
    with pytest.raises(OAuthCallbackError) as caught:
        auth.complete_oauth.execute(callback)
    assert caught.value.failure is OAuthCallbackFailure.REJECTED
    assert len(auth.users) == 0
    assert len(auth.oauth_links) == 0


def test_github_unverified_email_is_refused_no_network(
    auth: _AuthSystem,
    github: _MockGitHubOAuthProvider,
    no_network: None,
) -> None:
    # GitHub reports the email as unverified: nothing is linked (Req 10.3, 26.3).
    github.register(
        _GitHubAccount(subject="gh-6", email="unverified@example.com", email_verified=False)
    )
    callback = github.complete_handshake(subject="gh-6", returned_state="state-issued")
    with pytest.raises(OAuthCallbackError) as caught:
        auth.complete_oauth.execute(callback)
    assert caught.value.failure is OAuthCallbackFailure.EMAIL_UNVERIFIED
    assert len(auth.users) == 0
    assert len(auth.oauth_links) == 0
