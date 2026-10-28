"""Property-based test for invalid links never changing state (task 14.3).

Feature: foundations, Property 3: Invalid links never change state

Property 3 (design.md): *For any* verification or reset link that is expired,
already used, or unrecognised, opening it leaves the User's email-verification
state (verification links) or password (reset links) unchanged and returns an
invalid-or-expired response.

Scope note (reset links). Password-reset link consumption is implemented by
task 15, which is not yet done — there is no reset use case, fake, or store to
drive here. So this property is scoped to **verification** links, which task 12
fully implements (:class:`edu_it_application.verify_email.VerifyEmailUseCase`
rejects expired, already-used, and unrecognized links and leaves state
unchanged). The reset-link half of Property 3 (a stale reset link leaving the
password unchanged, Req 9.3) lands with task 15's reset property tests; this
file covers Req 7.5 in full and is written so the reset case can be added
alongside once task 15 exists.

This drives the real task-12 flow against the real infrastructure fakes —
:class:`In_Memory_UserRepository`, :class:`In_Memory_VerificationLinkRepository`,
:class:`Console_Mailer`, and :class:`Test_Clock` — exactly as the sign-up and
session property tests do, so the application flow and the fakes are exercised
together with no database, SMTP server, or real clock. Each example signs a
learner up to materialize a genuine unused verification link, then derives an
*invalid* opening of one of the three kinds the property names:

- **expired** — the Clock is driven to at-or-after issuance + the verification
  TTL, so the genuine link is past its lifetime (Req 7.3, 7.5);
- **already used** — the genuine link is consumed once (a real successful
  verification) and then opened a second time, which must be rejected as used
  (Req 7.4, 7.5);
- **unrecognized** — a token that was never issued is opened (Req 7.5).

For every invalid opening the property asserts the two observable guarantees:

1. **Rejected** — the result is :attr:`VerifyEmailOutcome.INVALID_OR_EXPIRED`
   and offers to resend (Req 7.5, 25.2).
2. **No state change** — a full snapshot of the user's verification status, the
   stored link's consumed flag, the user count, and the link count taken *before*
   the invalid opening is byte-for-byte equal to the snapshot taken *after*: the
   verification state is unchanged, the link-used flag is unchanged, and no new
   user or link record appeared (Req 7.5).

Validates: Requirements 7.5, 9.3

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count

from edu_it_application.sign_up import MAX_EMAIL_LENGTH, SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import (
    VerifyEmailOutcome,
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.config import PASSWORD_POLICY, VERIFICATION_LINK_TTL
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# --- Generators constrained to the property's input space --------------------

# A well-formed email no longer than 254 characters (Req 7.1), matching the
# sign-up use case's accepted shape exactly as the sign-up property test builds
# it: a non-empty local part of unreserved characters, a single "@", and a
# two-label domain. Lengths are kept small so the address stays within the
# 254-character maximum.
_EMAIL_LOCAL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_well_formed_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_LOCAL_ALPHABET, min_size=1, max_size=32),
    label=st.text(alphabet=_EMAIL_LOCAL_ALPHABET, min_size=1, max_size=32),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=8),
).filter(lambda email: len(email) <= MAX_EMAIL_LENGTH)

# A password satisfying the configured policy (Req 7.2): length within the
# inclusive 12..128 bound, kept in the accepted space so every sign-up succeeds.
_policy_compliant_passwords = st.text(
    min_size=PASSWORD_POLICY.min_length,
    max_size=PASSWORD_POLICY.max_length,
).filter(PASSWORD_POLICY.is_satisfied_by)

# How the opened verification link is made invalid. The three kinds are exactly
# the rejection causes Req 7.5 enumerates: expired, already used, and
# unrecognized.
_invalid_kinds = st.sampled_from(["expired", "already_used", "unrecognized"])

# For the "expired" kind, how far at/after issuance + TTL the Clock is driven.
# Expiry is strict (the link is invalid at or after issuance + TTL, Req 7.3), so
# zero (exactly at expiry) is already invalid; positive offsets are well past it.
_expired_offsets = st.sampled_from(
    [
        timedelta(0),
        timedelta(seconds=1),
        timedelta(hours=1),
        VERIFICATION_LINK_TTL,
    ]
)


@dataclass(frozen=True, slots=True)
class _StateSnapshot:
    """An immutable snapshot of every piece of state Property 3 must preserve.

    Captures the user's verification status, the stored link's consumed flag
    (``None`` when no link is stored — the unrecognized case), the number of
    users, and the full set of stored link tokens. Equality of two snapshots
    taken around an invalid opening proves the verification state, the link-used
    flag, the user count, and the link records (no new link appeared) were all
    left unchanged (Req 7.5).
    """

    status: str | None
    link_consumed: bool | None
    user_count: int
    link_tokens: frozenset[str]


def _make_use_cases() -> tuple[
    SignUpUseCase,
    VerifyEmailUseCase,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Console_Mailer,
    Test_Clock,
]:
    """Wire the sign-up and verification use cases to fresh infrastructure fakes.

    Mirrors the sign-up / session property tests: deterministic token and
    stable-id factories and a trivial password hasher so every example is
    reproducible under Hypothesis, with empty repositories so the single signed-up
    user and its one verification link are the only records.
    """
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer()
    clock = Test_Clock(start=_T0)
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())
    sign_up = SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )
    verify = VerifyEmailUseCase(users=users, verification_links=links, clock=clock)
    return sign_up, verify, users, links, mailer, clock


def _snapshot(
    users: In_Memory_UserRepository,
    links: In_Memory_VerificationLinkRepository,
    email: str,
    token: str,
) -> _StateSnapshot:
    """Snapshot the verification state the property must prove is unchanged."""
    user = users.get_by_email(email)
    stored = links.get(token)
    return _StateSnapshot(
        status=None if user is None else user.status,
        link_consumed=None if stored is None else stored.is_consumed,
        user_count=len(users),
        link_tokens=frozenset(links._by_token),
    )


@given(
    email=_well_formed_emails,
    password=_policy_compliant_passwords,
    kind=_invalid_kinds,
    expired_offset=_expired_offsets,
)
@settings(max_examples=200)
def test_property_3_invalid_links_never_change_state(
    email: str,
    password: str,
    kind: str,
    expired_offset: timedelta,
) -> None:
    """Feature: foundations, Property 3: Invalid links never change state.

    For any verification link that is expired, already used, or unrecognized,
    opening it is rejected as invalid/expired and leaves the user's verification
    status, the link's used flag, and the record counts unchanged.
    """
    sign_up, verify, users, links, mailer, clock = _make_use_cases()

    # Materialize one genuine, unused verification link by signing a learner up.
    sign_up.execute(SignUpRequest(email=email, password=password))
    token = mailer.sent[0].headers["X-Verification-Token"]

    # Shape the invalid opening of the chosen kind, then decide which token is
    # opened. Each branch leaves the store in the state the property snapshots.
    if kind == "unrecognized":
        # A token that was never issued; the genuine link is left untouched.
        opened_token = "never-issued-token"
    elif kind == "already_used":
        # Consume the genuine link once with a real, valid verification, then
        # open it a second time — the invalid (already-used) opening.
        first = verify.execute(VerifyEmailRequest(token=token))
        assert first.outcome is VerifyEmailOutcome.VERIFIED
        opened_token = token
    else:  # "expired"
        # Drive the Clock to at/after issuance + the TTL so the genuine link is
        # past its lifetime (Req 7.3), then open it — the invalid (expired)
        # opening.
        clock.set(_T0 + VERIFICATION_LINK_TTL + expired_offset)
        opened_token = token

    # Snapshot every piece of state the property must preserve, immediately
    # before the invalid opening (Req 7.5).
    before = _snapshot(users, links, email, token)

    result = verify.execute(VerifyEmailRequest(token=opened_token))

    # The invalid opening is rejected as invalid/expired and offers to resend
    # (Req 7.5, 25.2).
    assert result.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert result.offer_resend
    assert not result.is_verified

    # And it changed nothing: verification status, the link-used flag, the user
    # count, and the stored link tokens are all exactly as they were (Req 7.5).
    after = _snapshot(users, links, email, token)
    assert after == before
