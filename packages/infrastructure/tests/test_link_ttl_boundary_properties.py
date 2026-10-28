"""Property-based test for the link TTL expiry boundary (task 14.1).

Feature: foundations, Property 1: Links expire exactly at issuance + TTL as measured by the Clock

Property 1 (design.md): *For any* verification link (TTL 24 h) or reset link
(TTL 1 h) and any observation instant, opening the link is accepted only when the
observation instant is strictly before the issuance instant plus the link's TTL
as read through the injected Clock, and is rejected at or after that instant.

Scope this phase. Only verification links are implementable here: the sign-up and
verification-link-consumption use cases exist from task 12, but password reset —
and with it the reset link and its 1 h TTL — is task 15 and is not yet built
(tasks.md marks task 15 unstarted). So this property drives the verification link
and the verification TTL constant :data:`VERIFICATION_LINK_TTL` (24 h, Req 7.3);
the reset-link TTL (1 h, Req 9.1) is covered by this same property once task 15
lands and the reset link becomes exercisable.

This drives the real flow end to end against the infrastructure port fakes —
:class:`SignUpUseCase` issues a single-use verification link whose ``expires_at``
is ``issued_at`` + :data:`VERIFICATION_LINK_TTL` measured by the injected
:class:`Test_Clock`, and :class:`VerifyEmailUseCase` consumes it — wired exactly
as the test/dev composition root and the other sign-up / session property tests
wire them (:class:`In_Memory_UserRepository`,
:class:`In_Memory_VerificationLinkRepository`, :class:`Console_Mailer`,
:class:`Test_Clock`): no database, SMTP server, or real clock.

Each drawn example issues a link at a drawn issuance instant, then draws a
signed offset from the exact expiry boundary (``expires_at``) and positions the
Clock there, covering the three regions the boundary partitions over:

1. **just-before** (offset < 0): the observation instant is strictly before
   ``expires_at``; opening the link is accepted — the email is verified
   (Req 7.3).
2. **exactly-at** (offset == 0): the observation instant equals ``expires_at``;
   opening the link is rejected as invalid/expired and the user stays unverified
   — expiry is at issuance + TTL, not one tick later (Req 7.3).
3. **just-after** (offset > 0): the observation instant is after ``expires_at``;
   opening the link is rejected as invalid/expired and the user stays unverified
   (Req 7.3).

A fresh use-case / fakes set is wired per example so the single-use consumption
on the accepted path never contaminates the boundary observation: the property is
purely about the Clock-driven expiry boundary, which the single-use property
(task 14.2) covers separately. Across every example the biconditional holds:
verification succeeds if and only if the Clock reads strictly before
``issued_at`` + ``VERIFICATION_LINK_TTL``.

Validates: Requirements 7.3, 9.1

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

from edu_it_application.sign_up import SignUpRequest, SignUpUseCase
from edu_it_application.verify_email import VerifyEmailRequest, VerifyEmailUseCase
from edu_it_domain.config import PASSWORD_POLICY, VERIFICATION_LINK_TTL
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
)
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Generators constrained to the property's input space --------------------

# A well-formed email no longer than 254 characters: the minimal shape the
# sign-up use case accepts (a non-empty local part, one "@", a two-label domain),
# matching the sign-up property tests. The email only needs to be well-formed and
# unique within the empty repository; a single account is created per example.
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_well_formed_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

# A password satisfying the configured 12..128-character policy, so every drawn
# sign-up is valid and issues a verification link.
_policy_compliant_passwords = st.text(
    min_size=PASSWORD_POLICY.min_length,
    max_size=PASSWORD_POLICY.max_length,
).filter(PASSWORD_POLICY.is_satisfied_by)

# The locale stored on the account and mail; irrelevant to the expiry boundary.
_locales = st.sampled_from(["fr", "en"])

# The issuance instant: an arbitrary timezone-aware UTC datetime spanning a wide
# range, so the boundary is exercised at many absolute times rather than one
# fixed epoch. Hypothesis draws naive datetimes; they are marked UTC.
_issuance_instants = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2100, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))

# A signed offset from the exact expiry boundary (``expires_at``), covering the
# three regions the boundary partitions over: strictly-before (negative),
# exactly-at (zero), and after (positive). Offsets range from one microsecond —
# the finest resolution of ``datetime`` — up to well beyond the 24 h TTL, so both
# the razor's-edge boundary and comfortably-outside instants are exercised.
_boundary_offsets = st.one_of(
    st.just(timedelta(0)),
    st.builds(
        lambda micros: timedelta(microseconds=micros),
        micros=st.integers(min_value=1, max_value=60 * 60 * 1_000_000),
    ),
    st.builds(
        lambda micros: timedelta(microseconds=-micros),
        micros=st.integers(min_value=1, max_value=60 * 60 * 1_000_000),
    ),
)


def _make_use_cases() -> tuple[
    SignUpUseCase,
    VerifyEmailUseCase,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Console_Mailer,
    Test_Clock,
]:
    """Wire the sign-up and verification use cases to fresh infrastructure fakes.

    Mirrors the test/dev composition root and the other sign-up property tests:
    deterministic token / stable-id factories and a trivial password hasher, a
    single shared :class:`Test_Clock` so issuance and observation read the same
    controllable time, and empty stores so the one drawn account is the only one
    present.
    """
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer()
    clock = Test_Clock()
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
    email=_well_formed_emails,
    password=_policy_compliant_passwords,
    locale=_locales,
    issued_at=_issuance_instants,
    offset=_boundary_offsets,
)
@settings(max_examples=200)
def test_property_1_links_expire_exactly_at_issuance_plus_ttl(
    email: str,
    password: str,
    locale: str,
    issued_at: datetime,
    offset: timedelta,
) -> None:
    """Feature: foundations, Property 1: Links expire exactly at issuance + TTL as measured by the Clock.

    For any verification link and any observation instant, opening the link is
    accepted if and only if the Clock reads strictly before ``issued_at`` +
    ``VERIFICATION_LINK_TTL``, and rejected at or after that instant (Req 7.3;
    the reset-link TTL of Req 9.1 is covered by this property once task 15 lands).
    """
    sign_up, verify, users, links, mailer, clock = _make_use_cases()

    # Issue the verification link at the drawn issuance instant. The use case
    # stamps ``expires_at`` = ``issued_at`` + VERIFICATION_LINK_TTL from the
    # Clock, so the boundary under test is exactly that instant.
    clock.set(issued_at)
    sign_up.execute(SignUpRequest(email=email, password=password, locale=locale))

    created = users.get_by_email(email)
    assert created is not None
    assert created.status == USER_STATUS_UNVERIFIED

    token = mailer.sent[-1].headers["X-Verification-Token"]
    link = links.get(token)
    assert link is not None
    # The TTL boundary is driven by the Clock exactly as the constant dictates.
    expires_at = issued_at + VERIFICATION_LINK_TTL
    assert link.expires_at == expires_at

    # Position the Clock relative to the exact expiry boundary and open the link.
    observation = expires_at + offset
    clock.set(observation)
    result = verify.execute(VerifyEmailRequest(token=token))

    # The biconditional: the link is valid strictly before expiry and invalid at
    # or after it. ``offset < 0`` is the only accepted region; ``offset == 0``
    # (exactly at issuance + TTL) and ``offset > 0`` are both rejected.
    accepted_expected = observation < expires_at
    assert accepted_expected == (offset < timedelta(0))

    after = users.get_by_email(email)
    assert after is not None
    if accepted_expected:
        # Strictly before the boundary: verification succeeds and the email is
        # now verified (Req 7.3).
        assert result.is_verified
        assert after.status == USER_STATUS_VERIFIED
    else:
        # At or after the boundary: the link is rejected as invalid/expired, the
        # user stays unverified, and the boundary offers to resend (Req 7.3).
        assert not result.is_verified
        assert result.offer_resend
        assert after.status == USER_STATUS_UNVERIFIED
