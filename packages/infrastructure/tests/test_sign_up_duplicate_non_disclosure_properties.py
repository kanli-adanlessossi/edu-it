"""Property-based test for duplicate-email non-disclosure on sign-up (task 12.5).

Feature: foundations, Property 5: Duplicate-email sign-up does not disclose and does not duplicate

Property 5 (design.md): *For any* email already belonging to an existing User, a
repeat sign-up with that email creates no second User, returns the same
non-disclosing response as an accepted sign-up, and records a notification to the
existing owner in the Mailer.

This drives :class:`edu_it_application.sign_up.SignUpUseCase` directly, wired to
the infrastructure port fakes (:class:`In_Memory_UserRepository`,
:class:`In_Memory_VerificationLinkRepository`, :class:`Console_Mailer`,
:class:`Test_Clock`) and the injected token / stable-id / hash factories, exactly
as the test/dev composition root wires them — no database, SMTP server, or real
clock. Both infrastructure fakes and the application use case are imported here,
so the test lives beside the other property tests that cross those two layers
(the migration property tests follow the same placement).

Each drawn example registers a first, brand-new sign-up for an email (the
"owner"), then submits a *second* sign-up for the **same** email with a possibly
different password and locale. The property asserts, across every example:

1. **No second User** (Req 7.6): the user count is unchanged by the duplicate
   sign-up — no duplicate account is created — and no second verification link is
   issued.
2. **Non-disclosure** (Req 7.6): the duplicate sign-up returns a response
   byte-for-byte identical to the accepted result a fresh, independent sign-up
   returns, so the response cannot reveal that the email was already registered.
3. **Existing owner notified** (Req 7.7): the duplicate sign-up records exactly
   one additional notification to the existing owner's address in the Mailer.

The accepted response is deliberately identical on the new-user and duplicate
paths, so the two paths are distinguished only through side effects (the user
count and the mail recipient), never through the returned value.

Validates: Requirements 7.6, 7.7

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from itertools import count

from edu_it_application.sign_up import (
    ACCEPTED,
    SignUpRequest,
    SignUpResult,
    SignUpUseCase,
)
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# A well-formed email no longer than 254 characters: a short local part and a
# two-label domain, both drawn from a conservative alphabet, so every generated
# address is accepted by the sign-up validator (the property is about the
# duplicate path, not validation rejection).
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

# A policy-compliant password: 12-128 characters. The policy only checks length,
# so a printable string in that range always satisfies it.
_passwords = st.text(min_size=12, max_size=128)

_locales = st.sampled_from(["fr", "en"])


def _build_use_case() -> tuple[
    SignUpUseCase,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Console_Mailer,
]:
    """Wire a SignUpUseCase over fresh infrastructure fakes.

    Mirrors the test/dev composition: the in-memory repositories, the console
    mailer (writing to an in-memory stream so no output escapes the test), a
    Test_Clock fixed at a known instant, and deterministic token / stable-id /
    hash factories so the flow is fully reproducible for a drawn example.
    """
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    # Write to an in-memory buffer so the console mailer emits nothing to stdout
    # during the many property iterations while still retaining its sent record.
    mailer = Console_Mailer(stream=io.StringIO())
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


@given(
    email=_emails,
    owner_password=_passwords,
    duplicate_password=_passwords,
    owner_locale=_locales,
    duplicate_locale=_locales,
)
@settings(max_examples=200)
def test_property_5_duplicate_email_does_not_disclose_or_duplicate(
    email: str,
    owner_password: str,
    duplicate_password: str,
    owner_locale: str,
    duplicate_locale: str,
) -> None:
    """Feature: foundations, Property 5: Duplicate-email sign-up does not disclose and does not duplicate.

    A repeat sign-up for an already-registered email creates no second User,
    returns a response byte-for-byte identical to a fresh accepted sign-up, and
    records a notification to the existing owner in the Mailer.
    """
    use_case, users, links, mailer = _build_use_case()

    # Register the owner with a first, brand-new sign-up.
    owner_result = use_case.execute(
        SignUpRequest(email=email, password=owner_password, locale=owner_locale)
    )
    assert owner_result == ACCEPTED
    users_after_owner = len(users)
    # The owner's single-use verification link was minted as the first token;
    # a second account would mint the next token, so its absence proves the
    # duplicate path issued no new link.
    assert links.get("token-0") is not None
    mail_after_owner = len(mailer.sent)

    # The byte-for-byte reference response: an independent fresh sign-up (a
    # different, not-yet-registered email) over an isolated use case. The
    # duplicate response must match this exactly to be non-disclosing.
    fresh_use_case, _, _, _ = _build_use_case()
    fresh_result = fresh_use_case.execute(
        SignUpRequest(email=f"fresh-{email}", password=owner_password, locale=owner_locale)
    )

    # The duplicate sign-up: same email, possibly different password/locale.
    duplicate_result = use_case.execute(
        SignUpRequest(email=email, password=duplicate_password, locale=duplicate_locale)
    )

    # 1. No second User and no second verification link (Req 7.6): the user
    #    count is unchanged, and the token a second account would have minted was
    #    never issued.
    assert len(users) == users_after_owner
    assert links.get("token-1") is None

    # 2. Non-disclosure: the duplicate response is identical to both the fresh
    #    accepted response and the shared ACCEPTED result — nothing distinguishes
    #    the duplicate path from a fresh accepted sign-up (Req 7.6).
    assert duplicate_result == fresh_result
    assert duplicate_result == ACCEPTED
    assert isinstance(duplicate_result, SignUpResult)

    # 3. The existing owner was notified of the attempt: exactly one more mail,
    #    addressed to the owner's email (Req 7.7).
    assert len(mailer.sent) == mail_after_owner + 1
    assert mailer.sent[-1].to == email
    assert mailer.sent_to(email)[-1] is mailer.sent[-1]
