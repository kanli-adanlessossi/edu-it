"""Unit tests for verification-email delivery failure on sign-up (task 12.3, Req 24.1, 24.3).

These exercise :class:`edu_it_application.sign_up.SignUpUseCase` when the injected
``Mailer`` raises, against small in-test doubles for the domain ports (the
application layer depends only on the domain, so the tests do too). They assert
the design's failure contract (design, "Sign-up and email verification" and the
failure table): on a ``Mailer`` failure during sign-up the created user is kept
**unverified** (never rolled back), the verification link stays stored, the
failure is logged with safe non-disclosing fields, and a dedicated
:class:`~edu_it_application.sign_up.SignUpMailDeliveryError` is raised for the
boundary to render as a translated Problem_Details — after which the learner can
re-request the email (Req 24.1, 24.3).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from itertools import count

import pytest
from edu_it_application.sign_up import (
    SignUpMailDeliveryError,
    SignUpRequest,
    SignUpUseCase,
)
from edu_it_domain.ports.mailer import OutboundMail
from edu_it_domain.ports.repository import UserRecord
from edu_it_domain.ports.verification_link import VerificationLink

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_VALID_PASSWORD = "correct horse battery staple"  # 28 chars: within 12-128


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class _FakeUsers:
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

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover - unused here
        user = self.by_stable_id.get(stable_id)
        if user is None or user.status != "unverified":
            return False
        verified = replace(user, status="verified")
        self.by_stable_id[stable_id] = verified
        self.by_email[verified.email] = verified
        return True


class _FakeLinks:
    def __init__(self) -> None:
        self.by_token: dict[str, VerificationLink] = {}

    def add(self, link: VerificationLink) -> None:
        if link.token in self.by_token:
            raise ValueError("duplicate token")
        self.by_token[link.token] = link

    def get(self, token: str) -> VerificationLink | None:  # pragma: no cover - unused here
        return self.by_token.get(token)

    def mark_consumed(  # pragma: no cover - unused here
        self, token: str, *, consumed_at: datetime
    ) -> bool:
        return False


class _FailingMailer:
    """A :class:`Mailer` double that raises on the configured attempt(s).

    By default every ``send`` raises; ``fail_on`` can restrict the failure to a
    specific 1-based attempt so a later retry (resend) can succeed.
    """

    def __init__(self, *, fail_on: int | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.sent: list[OutboundMail] = []
        self._fail_on = fail_on
        self._error = error or RuntimeError("smtp: 421 service not available")

    def send(self, mail: OutboundMail) -> None:
        self.calls += 1
        if self._fail_on is None or self.calls == self._fail_on:
            raise self._error
        self.sent.append(mail)


def _make_use_case(
    *,
    users: _FakeUsers,
    links: _FakeLinks,
    mailer: _FailingMailer,
    clock: _FixedClock | None = None,
) -> SignUpUseCase:
    clock = clock if clock is not None else _FixedClock(_T0)
    tokens = (f"token-{n}" for n in count())
    ids = (f"user-{n}" for n in count())
    return SignUpUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: next(tokens),
        stable_id_factory=lambda: next(ids),
        password_hasher=lambda raw: f"hash:{raw}",
    )


def test_mail_failure_keeps_user_unverified_and_retains_link() -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _FailingMailer()
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    with pytest.raises(SignUpMailDeliveryError):
        use_case.execute(SignUpRequest(email="new@example.com", password=_VALID_PASSWORD))

    # The account is kept, unverified — not rolled back (Req 24.1).
    created = users.get_by_email("new@example.com")
    assert created is not None
    assert created.status == "unverified"
    # The verification link remains stored so a resend can reach the same user.
    assert len(links.by_token) == 1


def test_mail_failure_does_not_leak_provider_error_in_exception() -> None:
    users, links = _FakeUsers(), _FakeLinks()
    secret_text = "smtp: 535 auth failed for user admin@internal.example"
    mailer = _FailingMailer(error=RuntimeError(secret_text))
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    with pytest.raises(SignUpMailDeliveryError) as excinfo:
        use_case.execute(SignUpRequest(email="new@example.com", password=_VALID_PASSWORD))

    # The raised error carries no provider detail; the original error is only the
    # chained cause (for the logs), never part of the boundary-facing message
    # (Req 18.6, 19.6).
    assert secret_text not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert str(excinfo.value.__cause__) == secret_text


def test_mail_failure_logs_safe_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _FailingMailer()
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    with (
        caplog.at_level(logging.WARNING, logger="edu_it_application.sign_up"),
        pytest.raises(SignUpMailDeliveryError),
    ):
        use_case.execute(SignUpRequest(email="new@example.com", password=_VALID_PASSWORD))

    (record,) = [r for r in caplog.records if r.name == "edu_it_application.sign_up"]
    # The failure is recorded (Req 24.1) with stable, non-disclosing structured
    # fields — no recipient address, no provider error text in the message. The
    # structured fields are attached via ``extra=`` and read from the record's
    # ``__dict__`` (the same place the JSON formatter merges them from).
    fields = record.__dict__
    assert fields["event"] == "signup_verification_email_delivery_failed"
    assert fields["stage"] == "verification"
    assert fields["user_stable_id"] == "user-0"
    assert "new@example.com" not in record.getMessage()
    assert "smtp" not in record.getMessage().lower()


def test_duplicate_notice_mail_failure_creates_no_user_and_raises() -> None:
    users, links = _FakeUsers(), _FakeLinks()
    # First sign-up succeeds; the second (duplicate) hits a mail failure on the
    # owner-notice send.
    ok_then_fail = _FailingMailer(fail_on=2)
    use_case = _make_use_case(users=users, links=links, mailer=ok_then_fail)
    use_case.execute(SignUpRequest(email="owner@example.com", password=_VALID_PASSWORD))

    with pytest.raises(SignUpMailDeliveryError):
        use_case.execute(
            SignUpRequest(email="owner@example.com", password="another valid passphrase")
        )

    # The duplicate path created no second user regardless of the notice failure.
    assert len(users.by_stable_id) == 1


def test_learner_can_re_request_after_a_transient_failure() -> None:
    # A first attempt fails to deliver; a brand-new attempt for a *different*
    # email once the Mailer recovers succeeds end to end — demonstrating a mail
    # outage never corrupts the use case (Req 24.3). (Re-requesting the same
    # address is covered by the resend use case's tests.)
    users, links = _FakeUsers(), _FakeLinks()
    mailer = _FailingMailer(fail_on=1)
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    with pytest.raises(SignUpMailDeliveryError):
        use_case.execute(SignUpRequest(email="first@example.com", password=_VALID_PASSWORD))

    # The Mailer recovered; a later sign-up delivers normally.
    use_case.execute(SignUpRequest(email="second@example.com", password=_VALID_PASSWORD))
    assert mailer.sent[-1].to == "second@example.com"
    assert users.get_by_email("second@example.com") is not None
