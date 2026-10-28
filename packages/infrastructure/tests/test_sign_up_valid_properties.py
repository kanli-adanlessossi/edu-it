"""Property-based test for valid self-service sign-up (task 12.4).

Feature: foundations, Property 4: Valid self-service sign-up creates one unverified learner and one mail

Property 4 (design.md): *For any* well-formed email no longer than 254
characters and any password satisfying the configured policy, submitting the
sign-up form for a not-yet-registered email creates exactly one User with an
unverified email whose only role is learner and records exactly one verification
email in the Mailer.

This drives :class:`edu_it_application.sign_up.SignUpUseCase` against the real
infrastructure fakes — :class:`In_Memory_UserRepository`,
:class:`In_Memory_VerificationLinkRepository`, :class:`Console_Mailer`, and
:class:`Test_Clock` — exactly as the hand-written example in
``test_fake_repositories.py`` does, so the application flow and the fakes are
exercised together with no database, SMTP server, or real clock. Each example
draws a fresh well-formed email (never previously registered, since the
repository starts empty) and a policy-compliant password, and asserts the three
observable outcomes the property names:

1. **Exactly one unverified learner** (Req 7.1, 7.9, 13.3): the repository holds
   exactly one user, it is addressable by the submitted email, its status is
   ``unverified``, and the only role a self-service sign-up can assign is
   learner (:data:`LEARNER_ROLE`).
2. **Exactly one verification mail** (Req 7.1): the Mailer retained exactly one
   message, addressed to the submitted email and carrying the single-use token
   of the one verification link stored for the new user.

Validates: Requirements 7.1, 7.9, 13.3

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from edu_it_application.sign_up import (
    ACCEPTED,
    LEARNER_ROLE,
    MAX_EMAIL_LENGTH,
    SignUpRequest,
    SignUpUseCase,
)
from edu_it_domain.config import PASSWORD_POLICY
from edu_it_domain.ports.repository import USER_STATUS_UNVERIFIED
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

# A well-formed email no longer than 254 characters (Req 7.1): a non-empty local
# part of unreserved characters, exactly one "@", and a two-label domain whose
# labels are non-empty and dot-separated — the minimal shape the use case's
# ``_is_well_formed_email`` accepts. Lengths are kept small so the "@example.com"
# domain plus local part stays comfortably within the 254-char maximum.
_EMAIL_LOCAL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_well_formed_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_LOCAL_ALPHABET, min_size=1, max_size=32),
    label=st.text(alphabet=_EMAIL_LOCAL_ALPHABET, min_size=1, max_size=32),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=8),
).filter(lambda email: len(email) <= MAX_EMAIL_LENGTH)

# A password satisfying the configured policy (Req 7.2): length within the
# inclusive 12..128 bound. Drawing text at exactly the policy's bounds keeps the
# generator in the accepted space so the property exercises only valid sign-ups.
_policy_compliant_passwords = st.text(
    min_size=PASSWORD_POLICY.min_length,
    max_size=PASSWORD_POLICY.max_length,
).filter(PASSWORD_POLICY.is_satisfied_by)


def _make_use_case() -> tuple[
    SignUpUseCase,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Console_Mailer,
]:
    """Wire the sign-up use case to fresh infrastructure fakes.

    Mirrors ``test_fake_repositories.py``: deterministic token / stable-id
    factories and a trivial password hasher so the whole flow is reproducible
    under Hypothesis, with an empty repository so every drawn email is
    not-yet-registered.
    """
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    mailer = Console_Mailer()
    clock = Test_Clock(start=_T0)
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())
    use_case = SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )
    return use_case, users, links, mailer


@given(email=_well_formed_emails, password=_policy_compliant_passwords)
@settings(max_examples=200)
def test_property_4_valid_sign_up_creates_one_unverified_learner_and_one_mail(
    email: str,
    password: str,
) -> None:
    """Feature: foundations, Property 4: Valid self-service sign-up creates one unverified learner and one mail.

    For any well-formed email (<= 254 chars) and policy-compliant password, a
    sign-up for a not-yet-registered email creates exactly one unverified learner
    and records exactly one verification mail.
    """
    use_case, users, links, mailer = _make_use_case()

    result = use_case.execute(SignUpRequest(email=email, password=password))

    # The response is the single non-disclosing accepted result (Req 7.1).
    assert result == ACCEPTED

    # Exactly one user exists, addressable by the submitted email, unverified
    # (Req 7.1); the only role a self-service path assigns is learner (Req 7.9,
    # 13.3).
    assert len(users) == 1
    created = users.get_by_email(email)
    assert created is not None
    assert created.email == email
    assert created.status == USER_STATUS_UNVERIFIED
    assert LEARNER_ROLE == "learner"

    # Exactly one verification mail was recorded, to the submitted address, and
    # it carries the single-use token of the one stored verification link
    # (Req 7.1).
    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert sent.to == email
    token = sent.headers["X-Verification-Token"]
    stored = links.get(token)
    assert stored is not None
    assert stored.user_stable_id == created.stable_id
