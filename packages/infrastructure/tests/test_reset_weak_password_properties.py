"""Property-based test for weak-password rejection on reset completion (task 15.3).

Feature: foundations, Property 10: Reset rejects weak passwords without changing state

Property 10 (design.md): *For any* new password that does not satisfy the
configured password policy, submitting it through a valid reset link leaves the
User's existing password unchanged and returns a policy-failure response.

This drives :class:`edu_it_application.reset_password.CompletePasswordResetUseCase`
directly, wired to the infrastructure port fakes
(:class:`In_Memory_UserRepository`, :class:`In_Memory_CredentialStore`,
:class:`In_Memory_SessionRepository`, :class:`In_Memory_SessionStore`,
:class:`In_Memory_ResetLinkRepository`, :class:`Test_Clock`) and the injected
deterministic ``password_hasher`` and ``session_ref_factory``, exactly as the
test/dev composition root wires them — no database, Redis, or real clock. Both
the infrastructure fakes and the application use case are imported, so the test
lives beside the other property tests that cross those two layers.

Each drawn example stores exactly one valid, unexpired, unused reset link for a
registered user whose credential already holds a known *old* password, then
draws a *weak* password whose length falls outside the configured policy's
inclusive ``[12, 128]`` window — both the too-short (``< 12``) and too-long
(``> 128``) branches are generated — and completes the reset with it. The
property is that such a completion:

1. returns outcome :attr:`ResetCompletionOutcome.WEAK_PASSWORD`, and
2. changes **no** state — the stored credential still verifies the *old*
   password and not the drawn weak one; the reset link remains unused (not
   consumed) and so still usable; no new Session was established; and every prior
   Session still resolves and is not revoked (Req 9.4).

Validates: Requirements 9.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from edu_it_application.reset_password import (
    CompletePasswordResetRequest,
    CompletePasswordResetUseCase,
    ResetCompletionOutcome,
)
from edu_it_domain.config import PASSWORD_POLICY, RESET_LINK_TTL, SESSION_DURATION
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    SessionRecord,
    UserRecord,
)
from edu_it_domain.ports.reset_link import ResetLink
from edu_it_infrastructure.fakes import (
    In_Memory_CredentialStore,
    In_Memory_ResetLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_UserRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_USER_ID = "user-0"
_EMAIL = "learner@example.com"
_TOKEN = "reset-token-0"
_NEW_REF = "new-session-ref"

# The old password the credential holds before the reset attempt. It is itself
# policy-compliant (28 chars, within 12-128) so storing it is realistic, and the
# test asserts it survives the weak-password attempt untouched.
_OLD_PASSWORD = "correct horse battery staple"

# --- Generators constrained to the property's input space --------------------

# A weak password: one whose length falls *outside* the configured policy's
# inclusive [min_length, max_length] window, so PASSWORD_POLICY.is_satisfied_by
# is False by construction. Both failing branches are drawn — too short
# (0 .. min_length - 1 characters) and too long (max_length + 1 .. a bit beyond).
_TOO_SHORT = st.text(min_size=0, max_size=PASSWORD_POLICY.min_length - 1)
_TOO_LONG = st.text(
    min_size=PASSWORD_POLICY.max_length + 1,
    max_size=PASSWORD_POLICY.max_length + 32,
)
_weak_passwords = st.one_of(_TOO_SHORT, _TOO_LONG)

# How many prior Sessions the user holds before the reset attempt; each must
# survive the rejected completion unchanged. Zero is included so the property
# also covers the no-prior-session case.
_prior_session_counts = st.integers(min_value=0, max_value=4)


def _hash(raw: str) -> str:
    """The trivial deterministic "hash" the test/dev credential store compares."""
    return f"hash:{raw}"


def _make_use_case(
    prior_session_count: int,
) -> tuple[
    CompletePasswordResetUseCase,
    In_Memory_ResetLinkRepository,
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
    tuple[str, ...],
]:
    """Wire the reset-completion use case to fresh infrastructure fakes.

    Mirrors the test/dev composition root: a deterministic session-reference
    factory and a trivial password "hash" (the stored secret and the verified
    secret are compared for equality by :class:`In_Memory_CredentialStore`) so the
    whole flow is reproducible under Hypothesis. Seeds a registered user whose
    credential already holds :data:`_OLD_PASSWORD`, one valid/unexpired/unused
    reset link for that user, and ``prior_session_count`` live Sessions (durable
    record plus ephemeral state). Returns the live prior-session references so the
    caller can assert each survives the rejected completion.
    """
    users = In_Memory_UserRepository()
    credentials = In_Memory_CredentialStore()
    sessions = In_Memory_SessionRepository()
    reset_links = In_Memory_ResetLinkRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)

    users.add(
        UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=USER_STATUS_VERIFIED)
    )
    # Pre-store a known old credential so the test can assert it is untouched.
    credentials.set_password(_USER_ID, _hash(_OLD_PASSWORD))

    # One valid, unexpired, unused reset link issued at T0 (expires T0 + 1 h).
    reset_links.add(
        ResetLink(
            token=_TOKEN,
            user_stable_id=_USER_ID,
            issued_at=_T0,
            expires_at=_T0 + RESET_LINK_TTL,
        )
    )

    prior_refs = tuple(f"old-{n}" for n in range(prior_session_count))
    expires = _T0 + SESSION_DURATION
    for ref in prior_refs:
        sessions.add(
            SessionRecord(server_side_ref=ref, user_stable_id=_USER_ID, expires_at=expires)
        )
        session_store.create(ref, _USER_ID, expires_at=expires)

    refs = (f"{_NEW_REF}-{n}" for n in count())
    use_case = CompletePasswordResetUseCase(
        reset_links=reset_links,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        password_hasher=_hash,
        session_ref_factory=lambda: next(refs),
    )
    return use_case, reset_links, credentials, sessions, session_store, clock, prior_refs


@given(
    weak_password=_weak_passwords,
    prior_session_count=_prior_session_counts,
)
@settings(max_examples=200)
def test_property_10_reset_rejects_weak_passwords_without_changing_state(
    weak_password: str,
    prior_session_count: int,
) -> None:
    """Feature: foundations, Property 10: Reset rejects weak passwords without changing state.

    For any valid, unexpired, unused reset link and any password whose length
    falls outside the configured policy's inclusive window, completing the reset
    returns :attr:`ResetCompletionOutcome.WEAK_PASSWORD` and changes no state: the
    stored credential still verifies the old password and not the weak one, the
    link stays unused and usable, no new Session is established, and every prior
    Session still resolves and is unrevoked (Req 9.4).
    """
    (
        use_case,
        reset_links,
        credentials,
        sessions,
        session_store,
        _clock,
        prior_refs,
    ) = _make_use_case(prior_session_count)

    # Precondition: the drawn password genuinely fails the configured policy, so
    # the weak-password branch — not the invalid-link branch — is what is tested.
    assert not PASSWORD_POLICY.is_satisfied_by(weak_password)

    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=weak_password)
    )

    # 1. The outcome is the policy-failure response (Req 9.4).
    assert result.outcome is ResetCompletionOutcome.WEAK_PASSWORD
    assert not result.is_completed
    assert not result.offer_restart
    assert result.server_side_ref is None
    assert result.user_stable_id is None

    # 2a. The stored credential is unchanged: the OLD password still verifies and
    # the weak one does not (so the password was never replaced).
    assert credentials.verify_password(_USER_ID, _hash(_OLD_PASSWORD))
    assert not credentials.verify_password(_USER_ID, _hash(weak_password))

    # 2b. The reset link remains unused, so it is still usable for another attempt.
    stored_link = reset_links.get(_TOKEN)
    assert stored_link is not None
    assert not stored_link.is_consumed

    # 2c. No new Session was established (the factory was never called).
    assert sessions.get(f"{_NEW_REF}-0") is None
    assert session_store.resolve(f"{_NEW_REF}-0") is None

    # 2d. Every prior Session still resolves and is not revoked.
    for ref in prior_refs:
        durable = sessions.get(ref)
        assert durable is not None and not durable.is_revoked
        assert session_store.resolve(ref) is not None
