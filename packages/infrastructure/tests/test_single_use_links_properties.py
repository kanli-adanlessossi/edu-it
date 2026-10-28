"""Property-based test for single-use verification/reset links (task 14.2).

Feature: foundations, Property 2: Verification and reset links are single-use

Property 2 (design.md): *For any* verification or reset link, a first successful
use invalidates the link so that every subsequent use is rejected as already
used and produces no further state change.

This drives the real account flow, exactly as the sign-up / session property
tests do: a :class:`edu_it_application.sign_up.SignUpUseCase` mints the single-use
Verification_Link and records its verification mail, and a
:class:`edu_it_application.verify_email.VerifyEmailUseCase` consumes that same
link — both wired to the infrastructure port fakes
(:class:`In_Memory_UserRepository`, :class:`In_Memory_VerificationLinkRepository`,
:class:`Console_Mailer`, :class:`Test_Clock`) and the deterministic
token / stable-id / hash factories the test/dev composition root uses, so the
whole flow runs with no database, SMTP server, or real clock. Time is read only
through the injected :class:`Test_Clock`.

Each drawn example issues one verification link (via a real sign-up), then opens
it ``1 + repeats`` times at observation instants strictly before expiry, and
asserts across every example:

1. **First use succeeds and invalidates** (Req 7.4, 25.1): the first opening of
   the valid, unexpired, unused link verifies the owning user and marks the link
   consumed.
2. **Every subsequent use is rejected as already used** (Req 9.2, 25.1): each of
   the arbitrary number of later openings returns the invalid/expired outcome
   offering to resend.
3. **No further state change** (Req 25.1): after the first success, no later
   opening alters the user's verification state, the link's stored ``consumed_at``
   instant, or the Mailer's retained record — the repeated attempts leave the
   system exactly where the first success left it.

NOTE on reset links: Property 2 also covers single-use **Reset_Links**, but reset
links (task 15) are not yet implemented — there is no reset-request / reset-
completion use case or reset-link repository to drive. This test therefore scopes
the single-use property to Verification_Links (the single-use link flow that
exists after task 12). Reset-link single use is covered once task 15 lands and
can be folded into the same property, since the required behavior
(first-use-invalidates, every-later-use-rejected, no-further-state-change) is
identical.

Validates: Requirements 7.4, 9.2, 25.1

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from itertools import count

from edu_it_application.sign_up import ACCEPTED, SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import (
    VerifyEmailOutcome,
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.config import VERIFICATION_LINK_TTL
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED
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

# A well-formed email no longer than 254 characters: a short local part and a
# two-label domain drawn from a conservative alphabet, so every generated address
# is accepted by the sign-up validator (the property is about the single-use
# behavior of the issued link, not sign-up validation).
_email_atoms = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=12,
)
_emails = st.builds(
    lambda local, host: f"{local}@{host}.example",
    _email_atoms,
    _email_atoms,
)

# A policy-compliant password: 12-128 characters (the policy only checks length).
_passwords = st.text(min_size=12, max_size=128)

# "An arbitrary number of repeated consumption attempts" after the first success:
# at least one repeat, bounded to keep each example fast across 200 iterations.
_repeat_counts = st.integers(min_value=1, max_value=8)

# Offsets, strictly within the link's TTL window, at which each opening is
# attempted. The first offset drives the (successful) first use; the rest drive
# the repeated attempts. All are strictly before issuance + TTL, so every attempt
# fails only because the link is already used, never because it has expired —
# isolating the single-use property from the expiry property (Property 1).
_within_ttl_seconds = st.integers(
    min_value=0,
    max_value=int(VERIFICATION_LINK_TTL.total_seconds()) - 1,
)


def _build_flow() -> tuple[
    SignUpUseCase,
    VerifyEmailUseCase,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Console_Mailer,
    Test_Clock,
]:
    """Wire sign-up and verify-email use cases over one shared set of fakes.

    Mirrors the test/dev composition: both use cases share the same in-memory
    user and verification-link repositories and the same :class:`Test_Clock`, so
    a link minted by sign-up is the exact link the verification use case consumes.
    The console mailer writes to an in-memory buffer so nothing escapes to stdout
    across the many property iterations while still retaining its sent record.
    """
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer(stream=io.StringIO())
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


@given(
    email=_emails,
    password=_passwords,
    repeats=_repeat_counts,
    first_offset=_within_ttl_seconds,
    later_offsets=st.lists(_within_ttl_seconds, min_size=1, max_size=8),
)
@settings(max_examples=200)
def test_property_2_links_are_single_use(
    email: str,
    password: str,
    repeats: int,
    first_offset: int,
    later_offsets: list[int],
) -> None:
    """Feature: foundations, Property 2: Verification and reset links are single-use.

    A first successful use of a valid, unexpired, unused link invalidates it, so
    every subsequent use is rejected as already used and produces no further
    state change.
    """
    sign_up, verify, users, links, mailer, clock = _build_flow()

    # Issue exactly one single-use verification link through a real sign-up; the
    # mail carries its opaque token.
    assert sign_up.execute(SignUpRequest(email=email, password=password)) == ACCEPTED
    assert len(mailer.sent) == 1
    token = mailer.sent[0].headers["X-Verification-Token"]
    created = users.get_by_email(email)
    assert created is not None

    # --- First use: at an instant strictly before expiry, the link verifies ---
    clock.set(_T0 + timedelta(seconds=first_offset))
    first = verify.execute(VerifyEmailRequest(token=token))

    assert first.outcome is VerifyEmailOutcome.VERIFIED
    assert first.is_verified
    verified_user = users.get_by_email(email)
    assert verified_user is not None
    assert verified_user.status == USER_STATUS_VERIFIED
    consumed_link = links.get(token)
    assert consumed_link is not None
    assert consumed_link.is_consumed

    # The exact state snapshot the first success left behind. No later opening
    # may change any of it (Req 25.1).
    consumed_at_after_first = consumed_link.consumed_at
    mail_count_after_first = len(mailer.sent)

    # --- Repeated uses: an arbitrary number of later openings, all rejected ---
    # Build the repeated-attempt offsets, every one strictly within the TTL so a
    # rejection can only be "already used", never "expired".
    attempt_offsets = [later_offsets[i % len(later_offsets)] for i in range(repeats)]

    for offset in attempt_offsets:
        clock.set(_T0 + timedelta(seconds=offset))
        again = verify.execute(VerifyEmailRequest(token=token))

        # Every subsequent use is rejected as already used (Req 9.2, 25.1).
        assert again.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
        assert again.offer_resend
        assert not again.is_verified

        # No further state change: the user stays verified, the link's recorded
        # consume instant is unchanged, and no new mail was recorded (Req 25.1).
        still = users.get_by_email(email)
        assert still is not None
        assert still.status == USER_STATUS_VERIFIED
        link_now = links.get(token)
        assert link_now is not None
        assert link_now.is_consumed
        assert link_now.consumed_at == consumed_at_after_first
        assert len(mailer.sent) == mail_count_after_first
