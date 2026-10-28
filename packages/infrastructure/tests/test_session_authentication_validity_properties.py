"""Property-based test for session authentication validity (task 13.3).

Feature: foundations, Property 7: A session authenticates only while active

Property 7 (design.md): *For any* Session, a request presenting it is treated as
authenticated if and only if the Session is neither expired (as measured by the
Clock) nor revoked; an expired or revoked Session is rejected as unauthenticated
and is never refreshed or reissued.

This drives :class:`edu_it_application.authenticate.AuthenticateSessionUseCase`
against the real infrastructure fakes — :class:`In_Memory_SessionStore`,
:class:`In_Memory_SessionRepository`, and :class:`Test_Clock` — exactly as the
sign-up property tests drive their use case against the fakes, so the
application flow and the fakes are exercised together with no live Redis,
PostgreSQL, or real clock. A single Session is established for a user expiring
``SESSION_DURATION`` after issuance (measured by the Clock); each example then
drives the Clock to an instant relative to that expiry and optionally revokes
the Session, and asserts the one biconditional the property names:

- **Authenticated iff active** (Req 8.5): the use case resolves the Session to
  its owning user exactly when the Session is neither expired (the Clock is
  strictly before ``expires_at``) nor revoked; an expired Session, a revoked
  Session, or a Session that is both resolves to ``None`` — rejected as
  unauthenticated.
- **Never refreshed or reissued** (Req 8.5, 28.1): authenticating leaves the
  stored expiry and revoked flag unchanged, so a stale or revoked reference is
  never given a new lease or a new reference by the mere act of presenting it.

Validates: Requirements 8.5, 28.1

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    AuthenticateUseCase,
    SignInRequest,
)
from edu_it_domain.config import SESSION_DURATION
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    UserRecord,
)
from edu_it_infrastructure.fakes import (
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_SignInRateLimiter,
    In_Memory_UserRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_USER_ID = "user-1"
_EMAIL = "learner@example.com"
_PASSWORD = "correct horse battery staple"
_REF = "session-ref-1"

# The session expires at _T0 + SESSION_DURATION. An offset says where to drive
# the Clock relative to that expiry: a negative offset is strictly before expiry
# (active), zero is exactly at expiry (expired — expiry is strict, >=), and a
# positive offset is after expiry. The range brackets the boundary on both sides
# with a one-second granularity down to whole-day jumps, so the property covers
# just-before, exactly-at, and well-past expiry.
_expiry_offsets = st.sampled_from(
    [
        -SESSION_DURATION,
        -timedelta(days=1),
        -timedelta(seconds=1),
        timedelta(0),
        timedelta(seconds=1),
        timedelta(days=1),
        SESSION_DURATION,
    ]
)


def _make_use_cases() -> tuple[
    AuthenticateUseCase,
    AuthenticateSessionUseCase,
    In_Memory_SessionStore,
    In_Memory_SessionRepository,
    Test_Clock,
]:
    """Wire sign-in and session-authentication to fresh infrastructure fakes.

    A single verified user holds the known password, and a deterministic
    reference factory mints ``_REF`` so each example establishes exactly one
    Session at ``_T0`` expiring ``SESSION_DURATION`` later (per the Clock), which
    the property then ages or revokes.
    """
    users = In_Memory_UserRepository()
    users.add(
        UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=USER_STATUS_VERIFIED)
    )
    credentials = In_Memory_CredentialStore()
    credentials.set_password(_USER_ID, _PASSWORD)
    clock = Test_Clock(start=_T0)
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(clock)
    rate_limiter = In_Memory_SignInRateLimiter(clock)

    sign_in = AuthenticateUseCase(
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        rate_limiter=rate_limiter,
        clock=clock,
        session_ref_factory=lambda: _REF,
    )
    authenticate = AuthenticateSessionUseCase(session_store=session_store)
    return sign_in, authenticate, session_store, sessions, clock


@given(offset=_expiry_offsets, revoked=st.booleans())
@settings(max_examples=200)
def test_property_7_session_authenticates_only_while_active(
    offset: timedelta,
    revoked: bool,
) -> None:
    """Feature: foundations, Property 7: A session authenticates only while active.

    A presented Session resolves to its owning user if and only if it is neither
    expired (Clock strictly before expiry) nor revoked, and authenticating never
    refreshes or reissues the Session.
    """
    sign_in, authenticate, session_store, _, clock = _make_use_cases()

    # Establish exactly one Session for the user at _T0, expiring _T0 +
    # SESSION_DURATION as measured by the Clock (Req 8.1).
    result = sign_in.execute(SignInRequest(email=_EMAIL, password=_PASSWORD))
    assert result.server_side_ref == _REF
    expires_at = _T0 + SESSION_DURATION

    if revoked:
        session_store.revoke(_REF)

    # Drive the Clock to the instant under test, relative to the session's expiry.
    clock.set(expires_at + offset)

    # The session is active iff the Clock is strictly before expiry (expiry is
    # strict, >=) and it was not revoked (Req 8.5).
    expired = clock.now() >= expires_at
    expected_active = not revoked and not expired

    # Snapshot the stored state so we can prove authenticating changes nothing
    # (never refreshed or reissued, Req 8.5, 28.1).
    before = replace(session_store._by_ref[_REF])

    authed = authenticate.authenticate(_REF)

    if expected_active:
        # Authenticated: resolves to the owning user under the presented ref.
        assert authed is not None
        assert authed.user_stable_id == _USER_ID
        assert authed.server_side_ref == _REF
    else:
        # Expired, revoked, or both: rejected as unauthenticated (Req 8.5, 28.1).
        assert authed is None

    # The act of presenting the reference neither extended the expiry nor
    # cleared/flipped revocation nor minted a new reference: no refresh, no
    # reissue (Req 8.5, 28.1).
    after = session_store._by_ref[_REF]
    assert after.expires_at == before.expires_at
    assert after.revoked == before.revoked
    assert set(session_store._by_ref) == {_REF}
