"""Property-based test for idempotent sign-out (task 13.4).

Feature: foundations, Property 8: Sign-out revokes the current session and is idempotent

Property 8 (design.md): *For any* starting session state, signing out leaves no
current Session able to authenticate a subsequent request and returns success,
and signing out again still returns success.

This drives :class:`edu_it_application.authenticate.SignOutUseCase` directly,
wired to the infrastructure port fakes (:class:`In_Memory_SessionRepository`,
:class:`In_Memory_SessionStore`, :class:`Test_Clock`) and resolved against
:class:`edu_it_application.authenticate.AuthenticateSessionUseCase`, exactly as
the test/dev composition root wires them — no database, Redis, or real clock.
Both the infrastructure fakes and the application use cases are imported, so the
test lives beside the other property tests that cross those two layers.

Each drawn example starts from one of the possible "starting session states" the
property quantifies over, then signs out one or more times and asserts the two
guarantees the property names:

1. ``active`` — a live, unexpired session established for a user. The first
   sign-out revokes it (``revoked`` is ``True``) and afterward the reference no
   longer authenticates; every sign-out, including the first, returns success
   (Req 8.2).
2. ``already_expired`` — a session whose expiry has passed per the Clock. It
   already cannot authenticate; sign-out succeeds and reports nothing live was
   revoked (Req 8.2).
3. ``already_revoked`` — a session revoked before this sign-out. It already
   cannot authenticate; sign-out succeeds and reports nothing live was revoked
   (Req 8.2).
4. ``unknown_ref`` — a reference naming no session at all. Sign-out succeeds and
   reports nothing was revoked (Req 8.2).
5. ``none_ref`` — no reference is presented (``None``). Sign-out succeeds and
   reports nothing was revoked, the "no active Session is present" case the
   requirement calls out explicitly (Req 8.2).

In every case: (a) after the first sign-out the presented reference can no longer
authenticate a subsequent request, (b) every sign-out call returns success, and
(c) repeated sign-outs are idempotent — each extra call still succeeds, reports
nothing live was revoked, and leaves the same end state (the reference stays
unauthenticatable and the durable record stays revoked). That is the property.

Validates: Requirements 8.2

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_application.authenticate import (
    AuthenticateSessionUseCase,
    SignOutRequest,
    SignOutResult,
    SignOutUseCase,
)
from edu_it_domain.config import SESSION_DURATION
from edu_it_domain.ports.repository import SessionRecord
from edu_it_infrastructure.fakes import (
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_REF = "session-ref-0"
_USER_ID = "user-0"

# --- Generators constrained to the property's input space --------------------

# The "starting session state" the property quantifies over: a live session, one
# that has already lapsed (expired or revoked), or no current session at all
# (an unknown reference, or none presented).
_starting_states = st.sampled_from(
    [
        "active",
        "already_expired",
        "already_revoked",
        "unknown_ref",
        "none_ref",
    ]
)

# How many times sign-out is invoked in a row (>= 1): one sign-out exercises
# revocation and success; two or more additionally exercise idempotence.
_sign_out_counts = st.integers(min_value=1, max_value=5)


def _wire() -> tuple[
    SignOutUseCase,
    AuthenticateSessionUseCase,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the sign-out and session-authentication use cases to fresh fakes.

    Mirrors the test/dev composition root: both the durable
    :class:`In_Memory_SessionRepository` and the ephemeral
    :class:`In_Memory_SessionStore` (expiry decided by the injected
    :class:`Test_Clock`) back the use cases, so the whole flow is deterministic
    under Hypothesis with empty stores.
    """
    clock = Test_Clock(start=_T0)
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(clock)
    sign_out = SignOutUseCase(sessions=sessions, session_store=session_store, clock=clock)
    authenticate = AuthenticateSessionUseCase(session_store=session_store)
    return sign_out, authenticate, sessions, session_store, clock


@given(starting_state=_starting_states, sign_out_count=_sign_out_counts)
@settings(max_examples=200)
def test_property_8_sign_out_revokes_current_session_and_is_idempotent(
    starting_state: str,
    sign_out_count: int,
) -> None:
    """Feature: foundations, Property 8: Sign-out revokes the current session and is idempotent.

    For any starting session state, the first sign-out leaves the presented
    reference unable to authenticate a subsequent request and returns success,
    and every further sign-out still returns success without changing that end
    state (idempotent) — Req 8.2.
    """
    sign_out, authenticate, sessions, session_store, clock = _wire()

    # Build the starting state and decide the reference the caller presents.
    expires_at = _T0 + SESSION_DURATION
    presented_ref: str | None = _REF
    if starting_state in ("active", "already_expired", "already_revoked"):
        sessions.add(
            SessionRecord(
                server_side_ref=_REF,
                user_stable_id=_USER_ID,
                expires_at=expires_at,
            )
        )
        session_store.create(_REF, _USER_ID, expires_at=expires_at)
        if starting_state == "already_expired":
            # Move past expiry so the session is already inactive (per the Clock).
            clock.set(expires_at)
        elif starting_state == "already_revoked":
            # Revoke both stores before the sign-out under test.
            session_store.revoke(_REF)
            sessions.revoke(_REF, revoked_at=_T0)
    elif starting_state == "unknown_ref":
        # A reference naming no session at all.
        presented_ref = "no-such-ref"
    else:  # none_ref
        presented_ref = None

    # Only a session that is currently live can be revoked by this sign-out.
    first_is_live = starting_state == "active"

    # Sign out the drawn number of times (always at least once).
    results: list[SignOutResult] = [
        sign_out.execute(SignOutRequest(server_side_ref=presented_ref))
        for _ in range(sign_out_count)
    ]

    # (b) Every sign-out returns success — here, returns a result at all rather
    # than raising, which is what "success" means for this use case (Req 8.2).
    assert len(results) == sign_out_count
    assert all(isinstance(r, SignOutResult) for r in results)

    # The first sign-out reports whether a live session was actually revoked:
    # True only for the active starting state, False for every lapsed/absent one.
    assert results[0].revoked is first_is_live

    # (c) Idempotence: every sign-out after the first reports nothing live was
    # revoked, because the first already left no live session to revoke.
    assert all(r.revoked is False for r in results[1:])

    # (a) After signing out, the presented reference can no longer authenticate a
    # subsequent request — for every starting state (Req 8.2). An absent/unknown
    # reference never authenticated to begin with.
    assert authenticate.authenticate(presented_ref) is None

    # End state is stable across the repeated calls: when a real session was
    # presented, its durable record is revoked and stays revoked; the ephemeral
    # store no longer resolves it.
    if starting_state in ("active", "already_expired", "already_revoked"):
        durable = sessions.get(_REF)
        assert durable is not None
        assert durable.is_revoked
        assert session_store.resolve(_REF) is None
