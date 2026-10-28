"""Property-based test for reset-request non-disclosure (task 15.4).

Feature: foundations, Property 11: Reset requests do not disclose account existence

Property 11 (design.md): *For any* email, whether or not it is registered, a
password-reset request returns the byte-for-byte identical, non-disclosing
response; the only observable difference between a registered and an
unregistered email is a side effect (the reset link stored and the reset mail
sent), never the response — so the response cannot reveal whether the email
belongs to an existing account (Req 9.6).

This drives :class:`edu_it_application.reset_password.RequestPasswordResetUseCase`
directly, wired to the infrastructure port fakes (:class:`In_Memory_UserRepository`,
:class:`In_Memory_ResetLinkRepository`, :class:`Console_Mailer`,
:class:`Test_Clock`) and an injected deterministic ``token_factory``, exactly as
the test/dev composition root wires them — no database, SMTP server, or real
clock. Both the infrastructure fakes and the application use case are imported,
so the test lives beside the other property tests that cross those two layers.

Each drawn example draws a registered email and a *distinct* unregistered email,
then runs each through a fresh use-case instance. It asserts, across every
example:

1. **Identical response** (Req 9.6): the registered-branch result is
   byte-for-byte equal to the unregistered-branch result, and both equal the
   shared :data:`ACCEPTED` value — so nothing in the returned value distinguishes
   a registered email from an unregistered one.
2. **Side-effect asymmetry** (Req 9.6): for the registered email exactly one
   reset link is stored and exactly one mail is sent to that address; for the
   unregistered email no link is stored and no mail is sent. The two paths are
   distinguished only through these side effects, never through the response.

Because the response is identical while the side effects differ, the response
cannot disclose account existence; that is the property.

Validates: Requirements 9.6

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from edu_it_application.reset_password import (
    ACCEPTED,
    RequestPasswordResetRequest,
    RequestPasswordResetResult,
    RequestPasswordResetUseCase,
)
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    In_Memory_ResetLinkRepository,
    In_Memory_UserRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# --- Generators constrained to the property's input space --------------------

# A well-formed email: a short local part and a two-label domain drawn from a
# conservative alphabet. The property is about the request path (registered vs.
# not), so only distinctness and a stable address matter; the account is
# registered directly in the repository.
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

# A locale the registered account is stored with; irrelevant to the property but
# part of a UserRecord and of the reset mail the Mailer renders.
_locales = st.sampled_from(["fr", "en"])

_TOKEN = "reset-token-0"


def _build_use_case() -> tuple[
    RequestPasswordResetUseCase,
    In_Memory_UserRepository,
    In_Memory_ResetLinkRepository,
    Console_Mailer,
]:
    """Wire a RequestPasswordResetUseCase over fresh infrastructure fakes.

    Mirrors the test/dev composition: the in-memory repositories, the console
    mailer (writing to an in-memory stream so no output escapes the test), a
    Test_Clock fixed at a known instant, and a deterministic ``token_factory`` so
    the flow is fully reproducible for a drawn example.
    """
    users = In_Memory_UserRepository()
    reset_links = In_Memory_ResetLinkRepository()
    # Write to an in-memory buffer so the console mailer emits nothing to stdout
    # during the many property iterations while still retaining its sent record.
    mailer = Console_Mailer(stream=io.StringIO())
    clock = Test_Clock(start=_T0)
    use_case = RequestPasswordResetUseCase(
        users=users,
        reset_links=reset_links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: _TOKEN,
    )
    return use_case, users, reset_links, mailer


@given(
    registered_email=_emails,
    unregistered_email=_emails,
    locale=_locales,
)
@settings(max_examples=200)
def test_property_11_reset_requests_do_not_disclose_account_existence(
    registered_email: str,
    unregistered_email: str,
    locale: str,
) -> None:
    """Feature: foundations, Property 11: Reset requests do not disclose account existence.

    For any email, whether or not it is registered, a reset request returns the
    byte-for-byte identical non-disclosing response; the only observable
    difference is the side effect (a reset link stored and a reset mail sent for
    a registered email, nothing for an unregistered one), never the response, so
    the response cannot disclose account existence (Req 9.6).
    """
    # The two emails must differ: one names a registered account, the other names
    # none, and the property compares their two request outcomes.
    if registered_email == unregistered_email:
        unregistered_email = f"nobody-{unregistered_email}"
    assert registered_email != unregistered_email

    # The registered branch: a use case whose repository holds exactly the one
    # registered account.
    registered_uc, users, reg_links, reg_mailer = _build_use_case()
    users.add(
        UserRecord(
            stable_id="user-0",
            email=registered_email,
            locale=locale,
            status=USER_STATUS_VERIFIED,
        )
    )

    # The unregistered branch: an isolated use case whose repository holds no
    # account at all.
    unregistered_uc, _, unreg_links, unreg_mailer = _build_use_case()

    registered_result = registered_uc.execute(RequestPasswordResetRequest(email=registered_email))
    unregistered_result = unregistered_uc.execute(
        RequestPasswordResetRequest(email=unregistered_email)
    )

    # 1. Identical, non-disclosing response (Req 9.6): the two branches return the
    #    byte-for-byte identical result, which is the shared ACCEPTED value — so
    #    nothing in the response distinguishes a registered email from an
    #    unregistered one.
    assert registered_result == unregistered_result
    assert registered_result is ACCEPTED
    assert unregistered_result is ACCEPTED
    assert isinstance(registered_result, RequestPasswordResetResult)

    # 2. Side-effect asymmetry (Req 9.6): the registered email stored exactly one
    #    reset link and sent exactly one mail to that address; the unregistered
    #    email stored no link and sent no mail. The paths differ only here, never
    #    in the response above.
    stored_link = reg_links.get(_TOKEN)
    assert stored_link is not None
    assert stored_link.user_stable_id == "user-0"
    assert len(reg_mailer.sent) == 1
    assert reg_mailer.sent[0].to == registered_email

    assert unreg_links.get(_TOKEN) is None
    assert unreg_mailer.sent == ()
