"""Unit tests for the self-service sign-up use case (task 12.1, Req 7.1-7.3, 7.6-7.9, 13.3).

These exercise :class:`edu_it_application.sign_up.SignUpUseCase` directly against
small, self-contained in-test doubles for the domain ports — the application
layer depends only on the domain, so the tests do too (they do not import the
infrastructure fakes). The doubles implement exactly the port methods the use
case calls and let each example assert the observable outcome: how many users
exist, what mail was sent, the verification link's TTL as measured by the
injected clock, and which fields a rejected submission reports.

Covered behavior:

- A valid, not-yet-registered sign-up creates exactly one unverified learner and
  one verification mail, with the link expiring 24 h after issuance (Req 7.1-7.3,
  7.9, 13.3).
- A duplicate-email sign-up creates no second user, returns the identical
  non-disclosing result, and notifies the existing owner (Req 7.6, 7.7).
- A too-long or malformed email, and a policy-violating password, each raise a
  validation error naming exactly the rejected field(s) and change no state
  (Req 7.8).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from edu_it_application.sign_up import (
    ACCEPTED,
    LEARNER_ROLE,
    MAX_EMAIL_LENGTH,
    SignUpRequest,
    SignUpUseCase,
    SignUpValidationError,
)
from edu_it_domain.config import VERIFICATION_LINK_TTL
from edu_it_domain.ports.mailer import OutboundMail
from edu_it_domain.ports.repository import UserRecord
from edu_it_domain.ports.verification_link import VerificationLink

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_VALID_PASSWORD = "correct horse battery staple"  # 28 chars: within 12-128


class _FixedClock:
    """A minimal :class:`Clock` double returning a fixed, settable instant."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _FakeUsers:
    """A minimal :class:`UserRepository` double over a dict keyed by email."""

    def __init__(self) -> None:
        self.by_email: dict[str, UserRecord] = {}
        self.by_stable_id: dict[str, UserRecord] = {}

    def add(self, user: UserRecord) -> None:
        if user.stable_id in self.by_stable_id or user.email in self.by_email:
            raise ValueError("duplicate user")
        self.by_stable_id[user.stable_id] = user
        self.by_email[user.email] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        return self.by_email.get(email)

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover - unused by sign-up
        user = self.by_stable_id.get(stable_id)
        if user is None or user.status != "unverified":
            return False
        verified = replace(user, status="verified")
        self.by_stable_id[stable_id] = verified
        self.by_email[verified.email] = verified
        return True


class _FakeLinks:
    """A minimal :class:`VerificationLinkRepository` double over a dict."""

    def __init__(self) -> None:
        self.by_token: dict[str, VerificationLink] = {}

    def add(self, link: VerificationLink) -> None:
        if link.token in self.by_token:
            raise ValueError("duplicate token")
        self.by_token[link.token] = link

    def get(self, token: str) -> VerificationLink | None:
        return self.by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:  # pragma: no cover
        link = self.by_token.get(token)
        if link is None or link.is_consumed:
            return False
        self.by_token[token] = VerificationLink(
            token=link.token,
            user_stable_id=link.user_stable_id,
            issued_at=link.issued_at,
            expires_at=link.expires_at,
            consumed_at=consumed_at,
        )
        return True


class _RecordingMailer:
    """A minimal :class:`Mailer` double that retains every sent mail."""

    def __init__(self) -> None:
        self.sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        self.sent.append(mail)


def _make_use_case(
    *,
    users: _FakeUsers | None = None,
    links: _FakeLinks | None = None,
    mailer: _RecordingMailer | None = None,
    clock: _FixedClock | None = None,
) -> tuple[SignUpUseCase, _FakeUsers, _FakeLinks, _RecordingMailer, _FixedClock]:
    users = users if users is not None else _FakeUsers()
    links = links if links is not None else _FakeLinks()
    mailer = mailer if mailer is not None else _RecordingMailer()
    clock = clock if clock is not None else _FixedClock(_T0)
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
    return use_case, users, links, mailer, clock


def test_valid_sign_up_creates_one_unverified_learner_and_one_mail() -> None:
    use_case, users, links, mailer, _ = _make_use_case()

    result = use_case.execute(SignUpRequest(email="new@example.com", password=_VALID_PASSWORD))

    assert result == ACCEPTED
    # Exactly one user, unverified, locale carried through (Req 7.1).
    assert len(users.by_stable_id) == 1
    created = users.get_by_email("new@example.com")
    assert created is not None
    assert created.status == "unverified"
    # Exactly one verification mail to the new address (Req 7.1).
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "new@example.com"
    # Exactly one verification link was stored (Req 7.1).
    assert len(links.by_token) == 1


def test_verification_link_expires_24h_after_issuance_per_clock() -> None:
    use_case, _, links, _, clock = _make_use_case()

    use_case.execute(SignUpRequest(email="new@example.com", password=_VALID_PASSWORD))

    (link,) = links.by_token.values()
    # The TTL is the 24 h business constant measured from the Clock (Req 7.3).
    assert timedelta(hours=24) == VERIFICATION_LINK_TTL
    assert link.issued_at == clock.now()
    assert link.expires_at == clock.now() + VERIFICATION_LINK_TTL
    assert not link.is_consumed
    # The link is valid strictly before expiry and invalid at/after it.
    assert link.is_valid_at(link.expires_at - timedelta(seconds=1))
    assert not link.is_valid_at(link.expires_at)


def test_self_service_sign_up_role_is_learner_only() -> None:
    # The use case assigns only the learner role to a self-service sign-up
    # (Req 7.9, 13.3); the constant names that single role.
    assert LEARNER_ROLE == "learner"


def test_duplicate_email_creates_no_second_user_and_notifies_owner() -> None:
    use_case, users, links, mailer, _ = _make_use_case()
    first = use_case.execute(SignUpRequest(email="owner@example.com", password=_VALID_PASSWORD))
    assert first == ACCEPTED
    mail_after_first = len(mailer.sent)

    second = use_case.execute(
        SignUpRequest(email="owner@example.com", password="another valid passphrase")
    )

    # Non-disclosure: the duplicate response is identical in content (Req 7.6).
    assert second == first
    # No second user was created (Req 7.6).
    assert len(users.by_stable_id) == 1
    # No second verification link was issued for the duplicate.
    assert len(links.by_token) == 1
    # The existing owner was notified of the attempt (Req 7.7).
    assert len(mailer.sent) == mail_after_first + 1
    assert mailer.sent[-1].to == "owner@example.com"


@pytest.mark.parametrize(
    ("email", "reason"),
    [
        ("a" * 243 + "@example.com", "email_too_long"),  # 255 chars > 254
        ("not-an-email", "email_malformed"),
        ("missing-at-sign.example.com", "email_malformed"),
        ("spaces in@example.com", "email_malformed"),
    ],
)
def test_invalid_email_is_rejected_and_changes_no_state(email: str, reason: str) -> None:
    use_case, users, links, mailer, _ = _make_use_case()

    with pytest.raises(SignUpValidationError) as excinfo:
        use_case.execute(SignUpRequest(email=email, password=_VALID_PASSWORD))

    fields = {error.field: error.reason for error in excinfo.value.errors}
    assert fields == {"email": reason}
    # Nothing was created or sent (Req 7.8 rejects before any state change).
    assert not users.by_stable_id
    assert not links.by_token
    assert not mailer.sent


@pytest.mark.parametrize("password", ["short", "x" * 11, "y" * 129])
def test_weak_password_is_rejected_and_changes_no_state(password: str) -> None:
    use_case, users, _links, mailer, _ = _make_use_case()

    with pytest.raises(SignUpValidationError) as excinfo:
        use_case.execute(SignUpRequest(email="new@example.com", password=password))

    fields = {error.field: error.reason for error in excinfo.value.errors}
    assert fields == {"password": "password_policy_not_met"}
    assert not users.by_stable_id
    assert not mailer.sent


def test_boundary_length_email_and_password_are_accepted() -> None:
    use_case, users, _, _, _ = _make_use_case()
    # An email exactly at the 254-char maximum and a 12-char password (the policy
    # minimum) are both accepted (inclusive bounds, Req 7.1, 7.2).
    local = "a" * (MAX_EMAIL_LENGTH - len("@example.com"))
    email = f"{local}@example.com"
    assert len(email) == MAX_EMAIL_LENGTH

    result = use_case.execute(SignUpRequest(email=email, password="x" * 12))

    assert result == ACCEPTED
    assert len(users.by_stable_id) == 1


def test_both_fields_invalid_report_two_entries() -> None:
    use_case, _, _, _, _ = _make_use_case()

    with pytest.raises(SignUpValidationError) as excinfo:
        use_case.execute(SignUpRequest(email="bad", password="short"))

    fields = {error.field for error in excinfo.value.errors}
    assert fields == {"email", "password"}
