"""Property-based test for session-establishment conditions on sign-in (task 13.2).

Feature: foundations, Property 6: Sign-in establishes a session only for valid, verified credentials

Property 6 (design.md): *For any* account and credential submission, a
server-side Session associated with that User and expiring at the Clock's current
instant plus the configured duration is established if and only if the account's
email is verified and the credentials are valid; an unverified account, invalid
credentials, or an unknown account establishes no Session and returns a
translated, non-disclosing Problem_Details.

This drives :class:`edu_it_application.authenticate.AuthenticateUseCase` directly,
wired to the infrastructure port fakes (:class:`In_Memory_UserRepository`,
:class:`In_Memory_CredentialStore`, :class:`In_Memory_SessionRepository`,
:class:`In_Memory_SessionStore`, :class:`Test_Clock`) and the injected
session-reference factory, exactly as the test/dev composition root wires them —
no database, Redis, or real clock. Both the infrastructure fakes and the
application use case are imported, so the test lives beside the other property
tests that cross those two layers.

Each drawn example registers exactly one account for a well-formed email with a
known stored credential, then draws one of four mutually exclusive sign-in
scenarios and asserts the biconditional the property names:

1. ``verified_valid`` — a verified account with the correct password. A Session
   is established: ``execute`` returns a :class:`SignInResult` naming the user,
   the durable ``session`` record and the ephemeral ``SessionStore`` state both
   exist, and the session expires exactly at ``now + SESSION_DURATION`` measured
   by the Clock (Req 8.1).
2. ``unverified_valid`` — an unverified account with the correct password. No
   Session is established: ``execute`` raises :class:`SignInError` with the
   distinct ``EMAIL_NOT_VERIFIED`` reason and neither store holds a session
   (Req 8.3).
3. ``verified_wrong_password`` — a verified account with a wrong password. No
   Session is established: ``execute`` raises :class:`SignInError` with the
   non-disclosing ``INVALID_CREDENTIALS`` reason and neither store holds a
   session (Req 8.4).
4. ``unknown_account`` — an email that matches no User. No Session is
   established: ``execute`` raises :class:`SignInError` with the **same**
   ``INVALID_CREDENTIALS`` reason as the wrong-password case — so the refusal
   cannot disclose whether the account exists — and neither store holds a session
   (Req 8.4).

A Session is established on exactly the one scenario where the account is
verified *and* the credentials are valid, and on no other; that biconditional is
the property.

Validates: Requirements 8.1, 8.3, 8.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest
from edu_it_application.authenticate import (
    AuthenticateUseCase,
    SignInError,
    SignInFailure,
    SignInRequest,
    SignInResult,
)
from edu_it_domain.config import SESSION_DURATION
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
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

# --- Generators constrained to the property's input space --------------------

# A well-formed email: the same minimal accepted shape the sign-up property tests
# draw — a non-empty local part, one "@", and a two-label domain. The account is
# registered directly in the repository here, so only distinctness and a stable
# address matter.
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

# The stored password and a candidate submission. Drawing the submission
# independently lets the ``*_wrong_password`` scenario exercise a genuine
# mismatch; we guard equality explicitly where the scenario requires it.
_passwords = st.text(min_size=1, max_size=40)

# The four mutually exclusive sign-in scenarios the biconditional partitions over.
_scenarios = st.sampled_from(
    [
        "verified_valid",
        "unverified_valid",
        "verified_wrong_password",
        "unknown_account",
    ]
)


def _make_use_case() -> tuple[
    AuthenticateUseCase,
    In_Memory_UserRepository,
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the sign-in use case to fresh infrastructure fakes.

    Mirrors the test/dev composition root: a deterministic session-reference
    factory and a trivial password "hash" (the stored secret and the verified
    secret are compared for equality by :class:`In_Memory_CredentialStore`) so the
    whole flow is reproducible under Hypothesis, with empty stores so the drawn
    account is the only one present.
    """
    users = In_Memory_UserRepository()
    credentials = In_Memory_CredentialStore()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)
    rate_limiter = In_Memory_SignInRateLimiter(clock)
    refs = (f"ref-{n}" for n in count())
    use_case = AuthenticateUseCase(
        users=users,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        rate_limiter=rate_limiter,
        clock=clock,
        session_ref_factory=lambda: next(refs),
    )
    return use_case, users, credentials, sessions, session_store, clock


def _hash(raw: str) -> str:
    """The trivial deterministic "hash" the test/dev credential store compares."""
    return f"hash:{raw}"


@given(
    email=_emails,
    locale=_locales,
    stored_password=_passwords,
    submitted_password=_passwords,
    scenario=_scenarios,
)
@settings(max_examples=200)
def test_property_6_sign_in_establishes_session_only_for_valid_verified_credentials(
    email: str,
    locale: str,
    stored_password: str,
    submitted_password: str,
    scenario: str,
) -> None:
    """Feature: foundations, Property 6: Sign-in establishes a session only for valid, verified credentials.

    For any account and credential submission, a server-side Session (durable
    record plus ephemeral state, expiring at ``now + SESSION_DURATION``) is
    established if and only if the account is verified and the credentials are
    valid; an unverified account, a wrong password, or an unknown account
    establishes no Session and fails non-disclosingly (Req 8.1, 8.3, 8.4).
    """
    use_case, users, credentials, sessions, session_store, clock = _make_use_case()

    stable_id = "user-0"
    # Register the one account for every scenario except the unknown-account one,
    # where no User exists for the submitted email. A registered account always
    # has its correct credential stored.
    if scenario != "unknown_account":
        status = USER_STATUS_VERIFIED if scenario != "unverified_valid" else USER_STATUS_UNVERIFIED
        users.add(UserRecord(stable_id=stable_id, email=email, locale=locale, status=status))
        credentials.set_password(stable_id, _hash(stored_password))

    # Choose the submitted password so the scenario's credential validity holds.
    if scenario == "verified_wrong_password":
        # Force a genuine mismatch regardless of what was drawn.
        password = (
            submitted_password + "x"
            if submitted_password == stored_password
            else submitted_password
        )
        assert password != stored_password
    else:
        # Valid-credential scenarios (and the unknown-account one, where the
        # password is irrelevant) submit the correct password.
        password = stored_password

    # A Session is expected on exactly the verified-and-valid scenario.
    session_is_expected = scenario == "verified_valid"

    if session_is_expected:
        before = clock.now()
        result = use_case.execute(SignInRequest(email=email, password=_hash(password)))

        # A Session was established: the result names the user and a fresh ref.
        assert isinstance(result, SignInResult)
        assert result.user_stable_id == stable_id

        # The durable record exists, owns the user, and expires exactly at
        # now + SESSION_DURATION measured by the Clock (Req 8.1).
        durable = sessions.get(result.server_side_ref)
        assert durable is not None
        assert durable.user_stable_id == stable_id
        assert durable.expires_at == before + SESSION_DURATION
        assert not durable.is_revoked

        # The ephemeral state resolves to the same owner (Req 8.1, ADR 0004).
        resolved = session_store.resolve(result.server_side_ref)
        assert resolved is not None
        assert resolved.user_stable_id == stable_id
    else:
        # No Session is established on any refusal path (Req 8.3, 8.4).
        with pytest.raises(SignInError) as raised:
            use_case.execute(SignInRequest(email=email, password=_hash(password)))

        # The refusal reason matches the cause, and the invalid-credentials reason
        # is identical for a wrong password and an unknown account, so account
        # existence is never disclosed (Req 8.3, 8.4).
        if scenario == "unverified_valid":
            assert raised.value.failure == SignInFailure.EMAIL_NOT_VERIFIED
        else:
            assert raised.value.failure == SignInFailure.INVALID_CREDENTIALS

        # Neither store holds any session: no durable record for the minted ref
        # (none was minted) and the store resolves nothing.
        assert sessions.get("ref-0") is None
        assert session_store.resolve("ref-0") is None
