"""Unit tests for sign-in, sign-out, and session authentication (task 13.1, Req 8.1-8.5).

These exercise the three use cases in
:mod:`edu_it_application.authenticate` directly against small, self-contained
in-test doubles for the domain ports — the application layer depends only on the
domain, so the tests do too (they do not import the infrastructure fakes),
mirroring ``test_verify_email``. The doubles implement exactly the port methods
the use cases call and let each example assert the observable outcome: whether a
Session was established, whether a reference still authenticates, and the
non-disclosing refusal reason.

Covered behavior:

- Sign-in establishes a Session only for valid credentials on a verified account,
  expiring ``SESSION_DURATION`` after the Clock's current instant (Req 8.1).
- An unverified account (valid credentials) is refused with the "email not
  verified" reason and no Session (Req 8.3).
- Invalid credentials — a wrong password or an unknown account — are refused with
  the single, identical "invalid credentials" reason and no Session (Req 8.4).
- A presented reference authenticates only while active (neither expired per the
  Clock nor revoked), and is never refreshed or reissued (Req 8.5).
- Sign-out revokes the current Session and succeeds even when none is present,
  so it is idempotent (Req 8.2).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

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
from edu_it_domain.config import (
    SESSION_DURATION,
    SIGN_IN_RATE_LIMIT_THRESHOLD,
    SIGN_IN_RATE_LIMIT_WINDOW,
)
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    SessionRecord,
    UserRecord,
)
from edu_it_domain.ports.session_store import ResolvedSession

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_USER_ID = "user-1"
_EMAIL = "learner@example.com"
_PASSWORD = "correct horse battery staple"
_HASH = f"hash:{_PASSWORD}"
_REF = "session-ref-1"


class _MutableClock:
    """A minimal :class:`Clock` double whose instant can be set in tests."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class _FakeUsers:
    """A minimal :class:`UserRepository` double keyed by email / stable_id."""

    def __init__(self) -> None:
        self.by_stable_id: dict[str, UserRecord] = {}

    def add(self, user: UserRecord) -> None:
        self.by_stable_id[user.stable_id] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:  # pragma: no cover
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self.by_stable_id.values() if u.email == email), None)

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover
        return False


class _FakeCredentials:
    """A minimal :class:`CredentialStore` double over a dict of hashed secrets.

    Verification hashes the submitted password with the same ``hash:`` scheme the
    test stores, so a correct password matches and a wrong one does not; a user
    with no stored credential never matches (non-disclosing, Req 8.4).
    """

    def __init__(self) -> None:
        self.by_user: dict[str, str] = {}

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        self.by_user[user_stable_id] = hashed_secret

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        stored = self.by_user.get(user_stable_id)
        return stored is not None and stored == f"hash:{password}"


class _FakeSessions:
    """A minimal :class:`SessionRepository` double over a dict of durable records."""

    def __init__(self) -> None:
        self.by_ref: dict[str, SessionRecord] = {}

    def add(self, session: SessionRecord) -> None:
        if session.server_side_ref in self.by_ref:
            raise ValueError("duplicate session")
        self.by_ref[session.server_side_ref] = session

    def get(self, server_side_ref: str) -> SessionRecord | None:
        return self.by_ref.get(server_side_ref)

    def revoke(self, server_side_ref: str, *, revoked_at: datetime) -> bool:
        session = self.by_ref.get(server_side_ref)
        if session is None or session.is_revoked:
            return False
        self.by_ref[server_side_ref] = replace(session, revoked_at=revoked_at)
        return True

    def revoke_all_for_user(  # pragma: no cover
        self, user_stable_id: str, *, revoked_at: datetime
    ) -> int:
        return 0


class _FakeRateLimiter:
    """A minimal :class:`SignInRateLimiter` double: strict threshold, Clock window.

    Mirrors the Redis adapter's semantics the use case relies on — ``is_blocked``
    is true only once the current-window failure count exceeds the threshold, a
    window older than ``window`` (per the injected Clock) has elapsed so its count
    no longer applies, and ``reset`` clears the counter (Req 12.1, 12.2, 12.4).
    """

    def __init__(
        self,
        clock: _MutableClock,
        *,
        threshold: int = SIGN_IN_RATE_LIMIT_THRESHOLD,
        window: timedelta = SIGN_IN_RATE_LIMIT_WINDOW,
    ) -> None:
        self._clock = clock
        self._threshold = threshold
        self._window = window
        self._by_identifier: dict[str, tuple[datetime, int]] = {}

    def _current_count(self, identifier: str) -> int:
        entry = self._by_identifier.get(identifier)
        if entry is None:
            return 0
        window_start, count = entry
        if self._clock.now() - window_start >= self._window:
            return 0
        return count

    def is_blocked(self, identifier: str) -> bool:
        return self._current_count(identifier) > self._threshold

    def register_failure(self, identifier: str) -> None:
        now = self._clock.now()
        count = self._current_count(identifier)
        if count == 0:
            self._by_identifier[identifier] = (now, 1)
        else:
            window_start = self._by_identifier[identifier][0]
            self._by_identifier[identifier] = (window_start, count + 1)

    def reset(self, identifier: str) -> None:
        self._by_identifier.pop(identifier, None)


class _FakeSessionStore:
    """A minimal :class:`SessionStore` double: Clock-driven expiry, revoke flag."""

    def __init__(self, clock: _MutableClock) -> None:
        self._clock = clock
        self.entries: dict[str, dict[str, object]] = {}

    def create(self, server_side_ref: str, user_stable_id: str, *, expires_at: datetime) -> None:
        self.entries[server_side_ref] = {
            "user_stable_id": user_stable_id,
            "expires_at": expires_at,
            "revoked": False,
        }

    def resolve(self, server_side_ref: str) -> ResolvedSession | None:
        entry = self.entries.get(server_side_ref)
        if entry is None or entry["revoked"]:
            return None
        expires_at = entry["expires_at"]
        assert isinstance(expires_at, datetime)
        if self._clock.now() >= expires_at:
            return None
        user_stable_id = entry["user_stable_id"]
        assert isinstance(user_stable_id, str)
        return ResolvedSession(
            server_side_ref=server_side_ref,
            user_stable_id=user_stable_id,
            expires_at=expires_at,
        )

    def revoke(self, server_side_ref: str) -> bool:
        if self.resolve(server_side_ref) is None:
            return False
        self.entries[server_side_ref]["revoked"] = True
        return True

    def revoke_all_for_user(self, user_stable_id: str) -> int:  # pragma: no cover
        revoked_count = 0
        for ref in list(self.entries):
            if self.resolve(ref) is None:
                continue
            if self.entries[ref]["user_stable_id"] == user_stable_id:
                self.entries[ref]["revoked"] = True
                revoked_count += 1
        return revoked_count


def _setup(
    *,
    status: str = USER_STATUS_VERIFIED,
    with_credential: bool = True,
    rate_limiter: _FakeRateLimiter | None = None,
) -> tuple[
    AuthenticateUseCase,
    SignOutUseCase,
    AuthenticateSessionUseCase,
    _FakeUsers,
    _FakeSessions,
    _FakeSessionStore,
    _MutableClock,
    _FakeRateLimiter,
]:
    users = _FakeUsers()
    users.add(UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=status))
    credentials = _FakeCredentials()
    if with_credential:
        credentials.set_password(_USER_ID, _HASH)
    clock = _MutableClock(_T0)
    sessions = _FakeSessions()
    session_store = _FakeSessionStore(clock)
    limiter = rate_limiter if rate_limiter is not None else _FakeRateLimiter(clock)

    sign_in = AuthenticateUseCase(
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        rate_limiter=limiter,
        clock=clock,
        session_ref_factory=lambda: _REF,
    )
    sign_out = SignOutUseCase(sessions=sessions, session_store=session_store, clock=clock)
    authenticate = AuthenticateSessionUseCase(session_store=session_store)
    return sign_in, sign_out, authenticate, users, sessions, session_store, clock, limiter


# --- Sign-in: session establishment (Req 8.1) --------------------------------


def test_valid_verified_credentials_establish_a_session() -> None:
    sign_in, _, _, _, sessions, session_store, clock, _ = _setup()

    result = sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    # A Session is established for the user, carrying the opaque reference (Req 8.1).
    assert result.server_side_ref == _REF
    assert result.user_stable_id == _USER_ID
    # Both the durable record and the ephemeral state exist (ADR 0004).
    durable = sessions.get(_REF)
    assert durable is not None
    assert durable.user_stable_id == _USER_ID
    assert _REF in session_store.entries
    # Expiry is exactly now + SESSION_DURATION measured by the Clock (Req 8.1).
    assert durable.expires_at == clock.now() + SESSION_DURATION


def test_established_session_authenticates_the_user() -> None:
    sign_in, _, authenticate, _, _, _, _, _ = _setup()

    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    authed = authenticate.authenticate(_REF)

    assert authed is not None
    assert authed.user_stable_id == _USER_ID
    assert authed.server_side_ref == _REF


# --- Sign-in: refusals (Req 8.3, 8.4) ----------------------------------------


def test_unverified_account_is_refused_with_no_session() -> None:
    sign_in, _, _, _, sessions, session_store, _, _ = _setup(status=USER_STATUS_UNVERIFIED)

    with pytest.raises(SignInError) as caught:
        sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    # A valid credential on an unverified account reports the distinct reason (Req 8.3).
    assert caught.value.failure is SignInFailure.EMAIL_NOT_VERIFIED
    # No Session was established on the refusal path.
    assert not sessions.by_ref
    assert not session_store.entries


def test_wrong_password_is_refused_as_invalid_credentials() -> None:
    sign_in, _, _, _, sessions, session_store, _, _ = _setup()

    with pytest.raises(SignInError) as caught:
        sign_in.execute(SignInRequest(email=_EMAIL, password="wrong password here"))

    assert caught.value.failure is SignInFailure.INVALID_CREDENTIALS
    assert not sessions.by_ref
    assert not session_store.entries


def test_unknown_account_is_refused_with_same_reason_as_wrong_password() -> None:
    # Req 8.4: an unknown account and a wrong password must be indistinguishable.
    sign_in, _, _, _, sessions, _, _, _ = _setup()

    with pytest.raises(SignInError) as caught:
        sign_in.execute(SignInRequest(email="nobody@example.com", password=_PASSWORD))

    assert caught.value.failure is SignInFailure.INVALID_CREDENTIALS
    assert not sessions.by_ref


def test_unverified_account_with_wrong_password_hides_behind_invalid_credentials() -> None:
    # Credentials are checked first, so an unverified account with a wrong
    # password still reports only invalid credentials — the unverified state is
    # never disclosed to someone who cannot prove the password (Req 8.3, 8.4).
    sign_in, _, _, _, _, _, _, _ = _setup(status=USER_STATUS_UNVERIFIED)

    with pytest.raises(SignInError) as caught:
        sign_in.execute(SignInRequest(email=_EMAIL, password="wrong password here"))

    assert caught.value.failure is SignInFailure.INVALID_CREDENTIALS


# --- Session authentication while active (Req 8.5) ---------------------------


def test_absent_reference_does_not_authenticate() -> None:
    _, _, authenticate, _, _, _, _, _ = _setup()
    assert authenticate.authenticate(None) is None


def test_unknown_reference_does_not_authenticate() -> None:
    _, _, authenticate, _, _, _, _, _ = _setup()
    assert authenticate.authenticate("no-such-ref") is None


def test_expired_session_does_not_authenticate() -> None:
    sign_in, _, authenticate, _, _, _, clock, _ = _setup()
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    # At exactly now + SESSION_DURATION the session has expired (strict inequality).
    clock.set(_T0 + SESSION_DURATION)

    assert authenticate.authenticate(_REF) is None


def test_session_just_before_expiry_still_authenticates() -> None:
    sign_in, _, authenticate, _, _, _, clock, _ = _setup()
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    clock.set(_T0 + SESSION_DURATION - timedelta(seconds=1))

    assert authenticate.authenticate(_REF) is not None


def test_revoked_session_does_not_authenticate() -> None:
    sign_in, sign_out, authenticate, _, _, _, _, _ = _setup()
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    sign_out.execute(SignOutRequest(server_side_ref=_REF))

    assert authenticate.authenticate(_REF) is None


# --- Sign-out: revocation and idempotence (Req 8.2) --------------------------


def test_sign_out_revokes_the_current_session() -> None:
    sign_in, sign_out, _, _, sessions, _, clock, _ = _setup()
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    result = sign_out.execute(SignOutRequest(server_side_ref=_REF))

    assert result.revoked is True
    # The durable record is marked revoked too, keeping the system of record in step.
    durable = sessions.get(_REF)
    assert durable is not None
    assert durable.is_revoked
    assert durable.revoked_at == clock.now()


def test_sign_out_with_no_session_present_still_succeeds() -> None:
    _, sign_out, _, _, _, _, _, _ = _setup()

    result = sign_out.execute(SignOutRequest(server_side_ref=None))

    # Success with nothing revoked (Req 8.2).
    assert result.revoked is False


def test_sign_out_with_unknown_reference_still_succeeds() -> None:
    _, sign_out, _, _, _, _, _, _ = _setup()

    result = sign_out.execute(SignOutRequest(server_side_ref="no-such-ref"))

    assert result.revoked is False


def test_sign_out_is_idempotent() -> None:
    sign_in, sign_out, _, _, _, _, _, _ = _setup()
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    first = sign_out.execute(SignOutRequest(server_side_ref=_REF))
    second = sign_out.execute(SignOutRequest(server_side_ref=_REF))

    # The first revokes the active session; a repeat still succeeds with nothing
    # left to revoke, so sign-out is idempotent (Req 8.2).
    assert first.revoked is True
    assert second.revoked is False


# --- Sign-in: rate limiting (Req 12.1, 12.2, 12.4) ---------------------------


def test_sign_in_blocked_before_credentials_are_evaluated() -> None:
    # Req 12.1, 12.4: while blocked, the attempt is rejected without evaluating
    # credentials and establishes no Session even for valid credentials.
    limiter = _FakeRateLimiter(_MutableClock(_T0), threshold=2, window=SIGN_IN_RATE_LIMIT_WINDOW)
    sign_in, _, _, _, sessions, session_store, _, _ = _setup(rate_limiter=limiter)
    # Drive the identifier one over the threshold so it is blocked.
    for _ in range(3):
        limiter.register_failure(_EMAIL)

    with pytest.raises(SignInError) as caught:
        # Valid credentials for a verified account — still refused while blocked.
        sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    assert caught.value.failure is SignInFailure.RATE_LIMITED
    assert not sessions.by_ref
    assert not session_store.entries


def test_blocked_sign_in_does_not_touch_credentials() -> None:
    # Req 12.4: a blocked attempt must not evaluate the submitted credentials.
    clock = _MutableClock(_T0)
    limiter = _FakeRateLimiter(clock, threshold=1, window=SIGN_IN_RATE_LIMIT_WINDOW)
    for _ in range(2):
        limiter.register_failure(_EMAIL)
    users = _FakeUsers()
    users.add(
        UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=USER_STATUS_VERIFIED)
    )

    class _ExplodingCredentials:
        def set_password(self, user_stable_id: str, hashed_secret: str) -> None:  # pragma: no cover
            raise AssertionError("set_password is not used by sign-in")

        def verify_password(self, user_stable_id: str, password: str) -> bool:
            raise AssertionError("credentials must not be evaluated while blocked")

    sign_in = AuthenticateUseCase(
        users=users,
        credentials=_ExplodingCredentials(),
        sessions=_FakeSessions(),
        session_store=_FakeSessionStore(clock),
        rate_limiter=limiter,
        clock=clock,
        session_ref_factory=lambda: _REF,
    )

    with pytest.raises(SignInError) as caught:
        sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    assert caught.value.failure is SignInFailure.RATE_LIMITED


def test_failed_sign_in_registers_a_failure() -> None:
    # Req 12.1: a failed credential attempt counts toward the identifier's limit.
    limiter = _FakeRateLimiter(_MutableClock(_T0), threshold=2, window=SIGN_IN_RATE_LIMIT_WINDOW)
    sign_in, _, _, _, _, _, _, _ = _setup(rate_limiter=limiter)

    for _ in range(3):
        with pytest.raises(SignInError):
            sign_in.execute(SignInRequest(email=_EMAIL, password="wrong password here"))

    # Three failures over a threshold of 2 means the identifier is now blocked.
    assert limiter.is_blocked(_EMAIL) is True


def test_unverified_account_attempt_registers_a_failure() -> None:
    # Req 12.1: an unverified-account refusal is still a failed sign-in attempt.
    limiter = _FakeRateLimiter(_MutableClock(_T0), threshold=1, window=SIGN_IN_RATE_LIMIT_WINDOW)
    sign_in, _, _, _, _, _, _, _ = _setup(status=USER_STATUS_UNVERIFIED, rate_limiter=limiter)

    for _ in range(2):
        with pytest.raises(SignInError):
            sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    assert limiter.is_blocked(_EMAIL) is True


def test_successful_sign_in_resets_the_counter() -> None:
    # A legitimate user is not penalised by earlier failures once they succeed.
    limiter = _FakeRateLimiter(_MutableClock(_T0), threshold=3, window=SIGN_IN_RATE_LIMIT_WINDOW)
    sign_in, _, _, _, _, _, _, _ = _setup(rate_limiter=limiter)

    # Two failures, then a success clears the counter.
    for _ in range(2):
        with pytest.raises(SignInError):
            sign_in.execute(SignInRequest(email=_EMAIL, password="wrong password here"))
    sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))

    assert limiter.is_blocked(_EMAIL) is False
    assert limiter._current_count(_EMAIL) == 0


def test_attempts_allowed_again_once_the_window_elapses() -> None:
    # Req 12.2: once the window elapses per the Clock, attempts are allowed again.
    # The limiter and the use case must share one Clock so advancing it reopens
    # the window; build the limiter from the clock _setup wires everywhere.
    window = SIGN_IN_RATE_LIMIT_WINDOW
    clock = _MutableClock(_T0)
    users = _FakeUsers()
    users.add(
        UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=USER_STATUS_VERIFIED)
    )
    credentials = _FakeCredentials()
    credentials.set_password(_USER_ID, _HASH)
    sessions = _FakeSessions()
    session_store = _FakeSessionStore(clock)
    limiter = _FakeRateLimiter(clock, threshold=1, window=window)
    sign_in = AuthenticateUseCase(
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        rate_limiter=limiter,
        clock=clock,
        session_ref_factory=lambda: _REF,
    )

    for _ in range(2):
        with pytest.raises(SignInError):
            sign_in.execute(SignInRequest(email=_EMAIL, password="wrong password here"))
    assert limiter.is_blocked(_EMAIL) is True

    clock.set(_T0 + window)  # the window has elapsed

    # Valid credentials now succeed: the identifier is no longer blocked.
    result = sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert result.server_side_ref == _REF
    assert sessions.get(_REF) is not None
