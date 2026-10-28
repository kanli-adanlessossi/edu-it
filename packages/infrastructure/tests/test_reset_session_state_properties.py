"""Property-based test for post-reset session state (task 15.2).

Feature: foundations, Property 9: Successful reset leaves exactly the new session valid

Property 9: *For any* user with an arbitrary set of prior Sessions (including
none) and a valid, unexpired, unused reset link completed with a policy-compliant
password, *after* completion exactly the newly established Session is valid and no
prior Session is: every prior Session reference resolves to nothing through the
``SessionStore`` and its durable record is revoked (Req 28.1 — revoked by the
reset, never refreshed or reissued), while the new Session resolves, is owned by
the user, its durable record is not revoked, and it expires exactly at
``clock.now() + SESSION_DURATION`` (Req 28.2). Exactly one Session resolves
overall and it is the new one (Req 9.5).

This drives :class:`edu_it_application.reset_password.CompletePasswordResetUseCase`
directly, wired to the infrastructure port fakes
(:class:`In_Memory_UserRepository`, :class:`In_Memory_CredentialStore`,
:class:`In_Memory_SessionRepository`, :class:`In_Memory_SessionStore`,
:class:`In_Memory_ResetLinkRepository`, :class:`Test_Clock`) and the injected
password hasher and session-reference factory, exactly as the test/dev
composition root wires them — no database, Redis, or real clock. Both the
infrastructure fakes and the application use case are imported, so the test lives
beside the other property tests that cross those two layers.

Each drawn example registers one account, pre-establishes a varying number of
prior Sessions (including zero, to exercise the "all prior revoked" quantifier
over an empty set), stores one valid/unexpired/unused reset link, and completes
the reset with a policy-compliant password. It then asserts the biconditional
"exactly the new Session is valid" the property names: every prior reference
resolves to ``None`` and its durable record is revoked (Req 28.1); the new
reference resolves to the user with a live durable record expiring exactly at
``now + SESSION_DURATION`` (Req 28.2); and exactly one reference resolves
overall — the new one (Req 9.5).

Validates: Requirements 9.5, 28.1, 28.2

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
from edu_it_domain.config import RESET_LINK_TTL, SESSION_DURATION
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, SessionRecord, UserRecord
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
_TOKEN = "reset-token-0"

# --- Generators constrained to the property's input space --------------------

# A well-formed email: the same minimal accepted shape the sibling property tests
# draw — a non-empty local part, one "@", and a two-label domain. The account is
# registered directly in the repository here, so only a stable address matters.
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

# A locale the account is stored with; irrelevant to the property but part of a
# UserRecord.
_locales = st.sampled_from(["fr", "en"])

# A policy-compliant new password: the configured policy accepts 12-128
# characters, so draw within that band to keep every example a valid completion.
_strong_passwords = st.text(min_size=12, max_size=128)

# How many prior Sessions the user holds before the reset. Zero is included so the
# "every prior Session is revoked" quantifier is exercised over an empty set as
# well as over several.
_prior_session_counts = st.integers(min_value=0, max_value=6)


def _make_use_case() -> tuple[
    CompletePasswordResetUseCase,
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the reset-completion use case to fresh infrastructure fakes.

    Mirrors the test/dev composition root: a deterministic session-reference
    factory minting fresh ``new-ref-N`` references and a trivial password "hash"
    (``hash:<raw>``) so the whole flow is reproducible under Hypothesis, with
    empty stores so the drawn account is the only one present. The reset link is
    added by the caller once the owning user is known.
    """
    users = In_Memory_UserRepository()
    credentials = In_Memory_CredentialStore()
    sessions = In_Memory_SessionRepository()
    reset_links = In_Memory_ResetLinkRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)
    refs = (f"new-ref-{n}" for n in count())
    use_case = CompletePasswordResetUseCase(
        reset_links=reset_links,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        password_hasher=_hash,
        session_ref_factory=lambda: next(refs),
    )
    # The caller needs the stores and the clock to assert state and to seed the
    # account, the prior sessions, and the reset link before executing.
    _seed_account_and_link(users, credentials, reset_links)
    return use_case, credentials, sessions, session_store, clock


def _seed_account_and_link(
    users: In_Memory_UserRepository,
    credentials: In_Memory_CredentialStore,
    reset_links: In_Memory_ResetLinkRepository,
) -> None:
    """Register the one verified account, its old password, and a valid link."""
    users.add(
        UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status=USER_STATUS_VERIFIED)
    )
    credentials.set_password(_USER_ID, _hash("old password that is long enough"))
    reset_links.add(
        ResetLink(
            token=_TOKEN,
            user_stable_id=_USER_ID,
            issued_at=_T0,
            expires_at=_T0 + RESET_LINK_TTL,
        )
    )


# A fixed email/user is sufficient: the property is about the session set after a
# reset for one user, and the drawn email only needs to be a valid, stable
# address. It is referenced by the seeding helper above.
_EMAIL = "learner@example.com"


def _hash(raw: str) -> str:
    """The trivial deterministic "hash" the test/dev credential store compares."""
    return f"hash:{raw}"


def _prior_refs(count_: int) -> tuple[str, ...]:
    """The opaque references for ``count_`` prior Sessions, distinct from new ones."""
    return tuple(f"old-ref-{i}" for i in range(count_))


@given(
    new_password=_strong_passwords,
    prior_count=_prior_session_counts,
)
@settings(max_examples=200)
def test_property_9_successful_reset_leaves_exactly_the_new_session_valid(
    new_password: str,
    prior_count: int,
) -> None:
    """Feature: foundations, Property 9: Successful reset leaves exactly the new session valid.

    For any user with an arbitrary set of prior Sessions (including none) and a
    valid, unexpired, unused reset link completed with a policy-compliant
    password, after completion exactly the newly established Session is valid: no
    prior Session resolves and each prior durable record is revoked (Req 28.1),
    the new Session resolves to the user with a live durable record expiring
    exactly at ``now + SESSION_DURATION`` (Req 28.2), and exactly one Session
    resolves overall — the new one (Req 9.5).
    """
    use_case, _credentials, sessions, session_store, clock = _make_use_case()

    # Pre-establish the drawn number of prior Sessions (durable record + ephemeral
    # state), each live and owned by the user, before the reset runs.
    prior_refs = _prior_refs(prior_count)
    prior_expiry = clock.now() + SESSION_DURATION
    for ref in prior_refs:
        sessions.add(
            SessionRecord(server_side_ref=ref, user_stable_id=_USER_ID, expires_at=prior_expiry)
        )
        session_store.create(ref, _USER_ID, expires_at=prior_expiry)
    # Every prior Session resolves before completion, so the quantifier below has
    # something to revoke on each non-empty example.
    for ref in prior_refs:
        assert session_store.resolve(ref) is not None

    before = clock.now()
    result = use_case.execute(CompletePasswordResetRequest(token=_TOKEN, new_password=new_password))

    # The reset completed and named a fresh new Session for the user.
    assert result.outcome is ResetCompletionOutcome.COMPLETED
    assert result.user_stable_id == _USER_ID
    new_ref = result.server_side_ref
    assert new_ref is not None

    # Every prior Session is revoked by the reset: it resolves to nothing through
    # the store and its durable record is revoked, so it never refreshes or
    # reissues (Req 28.1).
    for ref in prior_refs:
        assert session_store.resolve(ref) is None
        prior_durable = sessions.get(ref)
        assert prior_durable is not None
        assert prior_durable.is_revoked

    # The new Session resolves, is owned by the user, its durable record is live,
    # and it expires exactly at now + SESSION_DURATION (Req 28.2).
    resolved = session_store.resolve(new_ref)
    assert resolved is not None
    assert resolved.user_stable_id == _USER_ID
    new_durable = sessions.get(new_ref)
    assert new_durable is not None
    assert not new_durable.is_revoked
    assert new_durable.expires_at == before + SESSION_DURATION

    # Exactly one Session resolves overall and it is the new one (Req 9.5).
    all_refs = (*prior_refs, new_ref)
    resolvable = [ref for ref in all_refs if session_store.resolve(ref) is not None]
    assert resolvable == [new_ref]
