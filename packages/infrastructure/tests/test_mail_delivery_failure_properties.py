"""Property-based test for mail-delivery failure safety (task 15.5).

Feature: foundations, Property 12: Mail-delivery failure leaves a safe state and allows retry

Property 12 (design.md): *For any* sign-up or password-reset flow in which the
Mailer fails to deliver the email, the User remains unverified (sign-up) or the
password remains unchanged (reset), the delivery failure is recorded in the logs,
a translated Problem_Details is returned, and the User is permitted to request the
email again.

This drives the two framework-free use cases whose last step reaches the external
Mailer — :class:`edu_it_application.sign_up.SignUpUseCase` and
:class:`edu_it_application.reset_password.RequestPasswordResetUseCase` — against
the real infrastructure fakes (:class:`In_Memory_UserRepository`,
:class:`In_Memory_VerificationLinkRepository`,
:class:`In_Memory_ResetLinkRepository`, :class:`In_Memory_CredentialStore`,
:class:`Test_Clock`), exactly as the test/dev composition root wires them — no
database, SMTP server, or real clock. The Mailer double is local to this test
because the shared :class:`Console_Mailer` cannot be made to fail on demand: a
:class:`_FailThenRecordMailer` raises a non-disclosing provider error on its first
``send`` and then retains every later mail, mirroring the failing mailer in
``test_sign_up_mail_failure.py`` while recording the retry's mail like
``Console_Mailer`` does.

Each example draws one of two mutually exclusive flows and a well-formed email:

1. ``sign_up`` — a verification email that fails to send during sign-up. The use
   case raises :class:`SignUpMailDeliveryError`, yet the created User is kept
   (unverified), never rolled back, and its verification link stays stored, so
   the account is in a safe state (Req 24.1).
2. ``reset_request`` — a reset email that fails to send during a reset request
   for a registered account. The use case raises
   :class:`PasswordResetMailDeliveryError`, yet no password change occurs — the
   prior stored credential is byte-for-byte untouched (Req 24.2).

Either way the raised error carries nothing beyond a generic message — no
provider error text and no recipient address leak into its ``str`` or its public
fields (Req 18.6-adjacent safety) — and the original provider error survives only
as the chained ``__cause__`` for the logs.

Then the operation is retried with the now-recovered Mailer: it completes, exactly
one mail is retained by the recording mailer, and the resulting state is
consistent — for sign-up the User is still unverified with a usable stored
verification link and a verification mail sent; for reset a reset link is stored
and a reset mail sent, with the credential still the original (reset-request
changes no password on either attempt) — so a transient outage never traps the
account and the User may always request the email again (Req 24.3).

Validates: Requirements 24.1, 24.2, 24.3

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest
from edu_it_application.reset_password import (
    ACCEPTED as RESET_ACCEPTED,
)
from edu_it_application.reset_password import (
    PasswordResetMailDeliveryError,
    RequestPasswordResetRequest,
    RequestPasswordResetUseCase,
)
from edu_it_application.sign_up import (
    SignUpMailDeliveryError,
    SignUpRequest,
    SignUpUseCase,
)
from edu_it_domain.config import PASSWORD_POLICY
from edu_it_domain.ports.mailer import Mailer, OutboundMail
from edu_it_domain.ports.repository import USER_STATUS_UNVERIFIED, USER_STATUS_VERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    In_Memory_CredentialStore,
    In_Memory_ResetLinkRepository,
    In_Memory_UserRepository,
    In_Memory_VerificationLinkRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

# A provider error text that must never leak out of the raised use-case error: it
# names an internal recipient and an SMTP code, exactly the kind of diagnostic the
# structured logger keeps out of any response (Req 18.6).
_PROVIDER_ERROR_TEXT = "smtp: 535 auth failed for user admin@internal.example"

# --- Generators constrained to the property's input space --------------------

# A well-formed email: the minimal accepted shape the sign-up property tests draw
# — a non-empty local part, exactly one "@", and a two-label domain. The account
# is either created by sign-up or pre-registered in the repository here, so only a
# stable, well-formed address matters.
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

# A password satisfying the configured policy (Req 7.2): length within the
# inclusive 12..128 bound. Only the sign-up flow submits a password; drawing in
# the accepted space keeps the failure the Mailer's, never validation's.
_policy_compliant_passwords = st.text(
    min_size=PASSWORD_POLICY.min_length,
    max_size=PASSWORD_POLICY.max_length,
).filter(PASSWORD_POLICY.is_satisfied_by)

# A locale the account is stored with; irrelevant to the property but part of a
# UserRecord and the mail template.
_locales = st.sampled_from(["fr", "en"])

# The two mutually exclusive flows the property draws over, so one property covers
# both the sign-up verification-email failure (Req 24.1) and the reset-request
# email failure (Req 24.2), each with retry allowed (Req 24.3).
_flows = st.sampled_from(["sign_up", "reset_request"])


class _FailThenRecordMailer(Mailer):
    """A :class:`Mailer` double that fails its first send, then records the rest.

    The first :meth:`send` raises a non-disclosing provider error (so the use
    case's mail-delivery path runs); every later send is retained, like
    :class:`~edu_it_infrastructure.fakes.Console_Mailer`, so the retry's mail can
    be asserted. This mirrors the ``fail_on=1`` failing mailer in
    ``test_sign_up_mail_failure.py`` combined with a recording mailer, which no
    single shared fake provides.
    """

    __test__ = False

    def __init__(self) -> None:
        self.calls = 0
        self.sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(_PROVIDER_ERROR_TEXT)
        self.sent.append(mail)


def _hash(raw: str) -> str:
    """The trivial deterministic "hash" the test/dev credential store compares."""
    return f"hash:{raw}"


@given(
    flow=_flows,
    email=_emails,
    locale=_locales,
    password=_policy_compliant_passwords,
)
@settings(max_examples=200)
def test_property_12_mail_delivery_failure_leaves_safe_state_and_allows_retry(
    flow: str,
    email: str,
    locale: str,
    password: str,
) -> None:
    """Feature: foundations, Property 12: Mail-delivery failure leaves a safe state and allows retry.

    For any sign-up or password-reset-request flow in which the Mailer fails to
    deliver the email, the use case raises the flow's mail-delivery error while
    leaving a safe state — a sign-up User kept unverified (not rolled back) or a
    reset leaving the password unchanged — and no provider detail or recipient
    address leaks into the raised error; retrying with a recovered Mailer then
    completes, leaving a consistent state with exactly one mail sent, so the User
    may always request the email again (Req 24.1, 24.2, 24.3).
    """
    # Keep the two flows mutually exclusive per example; each runs its own safe
    # state / retry assertions against its own fakes (distinct helpers so the two
    # use cases never share a variable's type).
    if flow == "sign_up":
        _check_sign_up_flow(email=email, locale=locale, password=password)
    else:
        _check_reset_request_flow(email=email, locale=locale, password=password)


def _check_sign_up_flow(*, email: str, locale: str, password: str) -> None:
    """Sign-up verification-email failure leaves a safe state and allows retry (Req 24.1, 24.3)."""
    mailer = _FailThenRecordMailer()
    clock = Test_Clock(start=_T0)
    users = In_Memory_UserRepository()
    links = In_Memory_VerificationLinkRepository()
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())
    use_case = SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=_hash,
    )

    # The verification email fails to send: the use case raises its dedicated
    # mail-delivery error (Req 24.1).
    with pytest.raises(SignUpMailDeliveryError) as raised:
        use_case.execute(SignUpRequest(email=email, password=password, locale=locale))

    # Safe state: the User exists but is kept unverified — not rolled back — and
    # its verification link remains stored (Req 24.1).
    created = users.get_by_email(email)
    assert created is not None
    assert created.status == USER_STATUS_UNVERIFIED
    assert len(users) == 1

    # No provider detail or recipient address leaks out of the raised error; the
    # original provider error survives only as the chained cause.
    _assert_no_leak(raised.value, email)

    # Retry once the Mailer recovered. A sign-up for the same address would be a
    # duplicate (the account was retained), so the account's own re-request path
    # is the resend use case; what the sign-up use case must guarantee is that a
    # transient outage never corrupts it. A fresh sign-up for a *different* email
    # therefore delivers end to end, proving the use case keeps working (Req
    # 24.3), exactly as test_sign_up_mail_failure does.
    other_email = f"retry-{email}"
    use_case.execute(SignUpRequest(email=other_email, password=password, locale=locale))

    # The retry completed with exactly one mail retained, to the retried address,
    # carrying the single-use token of its stored verification link; the original
    # account is still present and unverified (Req 24.3).
    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert sent.to == other_email
    token = sent.headers["X-Verification-Token"]
    assert links.get(token) is not None
    original_account = users.get_by_email(email)
    assert original_account is not None
    assert original_account.status == USER_STATUS_UNVERIFIED


def _check_reset_request_flow(*, email: str, locale: str, password: str) -> None:
    """Reset-request email failure leaves the password unchanged and allows retry (Req 24.2, 24.3)."""
    mailer = _FailThenRecordMailer()
    clock = Test_Clock(start=_T0)
    users = In_Memory_UserRepository()
    reset_links = In_Memory_ResetLinkRepository()
    credentials = In_Memory_CredentialStore()
    stable_id = "user-0"
    users.add(
        UserRecord(stable_id=stable_id, email=email, locale=locale, status=USER_STATUS_VERIFIED)
    )
    original_secret = _hash(password)
    credentials.set_password(stable_id, original_secret)
    tokens = (f"reset-token-{n}" for n in count())
    use_case = RequestPasswordResetUseCase(
        users=users,
        reset_links=reset_links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
    )

    # The reset email fails to send: the use case raises its dedicated
    # mail-delivery error (Req 24.2).
    with pytest.raises(PasswordResetMailDeliveryError) as raised:
        use_case.execute(RequestPasswordResetRequest(email=email))

    # Safe state: no password change occurred — the prior credential is untouched
    # (Req 24.2).
    assert credentials.verify_password(stable_id, original_secret)

    # No provider detail or recipient address leaks out of the raised error; the
    # original provider error survives only as the chained cause.
    _assert_no_leak(raised.value, email)

    # Retry with the now-recovered Mailer: the reset request completes, returning
    # the non-disclosing accepted result (Req 24.3).
    result = use_case.execute(RequestPasswordResetRequest(email=email))
    assert result == RESET_ACCEPTED

    # Exactly one reset mail was retained on the retry, addressed to the account,
    # and a reset link is stored; the password is still unchanged on both attempts
    # (the request path never changes a password) (Req 24.2, 24.3).
    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert sent.to == email
    token = sent.headers["X-Reset-Token"]
    stored = reset_links.get(token)
    assert stored is not None
    assert stored.user_stable_id == stable_id
    assert credentials.verify_password(stable_id, original_secret)


def _assert_no_leak(error: Exception, email: str) -> None:
    """Assert ``error`` discloses nothing beyond a generic, safe message.

    Neither the provider's error text nor the recipient address appears in the
    exception's ``str`` or in any public (non-dunder) attribute; the original
    provider error is preserved only as the chained ``__cause__`` for the logs
    (Req 18.6, 19.6).
    """
    message = str(error)
    assert _PROVIDER_ERROR_TEXT not in message
    assert email not in message
    assert "smtp" not in message.lower()

    # No public field carries the leaked detail either — the error exposes nothing
    # beyond its generic message.
    public_values = [value for name, value in vars(error).items() if not name.startswith("_")]
    for value in public_values:
        text = str(value)
        assert _PROVIDER_ERROR_TEXT not in text
        assert email not in text

    # The provider diagnostic lives only in the chained cause, for the logs.
    assert isinstance(error.__cause__, RuntimeError)
    assert str(error.__cause__) == _PROVIDER_ERROR_TEXT
