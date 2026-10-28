"""Unit tests for re-requesting a verification email (task 12.3, Req 24.3, 25.2).

These exercise :class:`edu_it_application.resend_verification.ResendVerificationUseCase`
against small in-test doubles for the domain ports. They assert the design's
"the User may request the email again" contract: an unverified user gets a fresh
single-use link and a new mail; an already-verified user and an unknown email
change nothing; every outcome returns the identical non-disclosing result; and a
``Mailer`` failure leaves the user unverified, is logged, and raises a dedicated
error for the boundary while still permitting another attempt (Req 24.1, 24.3).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from edu_it_application.resend_verification import (
    ACCEPTED,
    ResendVerificationError,
    ResendVerificationRequest,
    ResendVerificationUseCase,
)
from edu_it_domain.config import VERIFICATION_LINK_TTL
from edu_it_domain.ports.mailer import OutboundMail
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    UserRecord,
)
from edu_it_domain.ports.verification_link import VerificationLink

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


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
        self.by_stable_id[user.stable_id] = user
        self.by_email[user.email] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:  # pragma: no cover - unused
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        return self.by_email.get(email)

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover - unused here
        return False


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


class _RecordingMailer:
    def __init__(self) -> None:
        self.sent: list[OutboundMail] = []

    def send(self, mail: OutboundMail) -> None:
        self.sent.append(mail)


class _FailingMailer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("smtp: 421 service not available")

    def send(self, mail: OutboundMail) -> None:
        raise self.error


def _unverified(email: str = "learner@example.com", locale: str = "en") -> UserRecord:
    return UserRecord(
        stable_id="user-0",
        email=email,
        locale=locale,
        status=USER_STATUS_UNVERIFIED,
    )


def _make_use_case(
    *,
    users: _FakeUsers,
    links: _FakeLinks,
    mailer: object,
    clock: _FixedClock | None = None,
) -> ResendVerificationUseCase:
    clock = clock if clock is not None else _FixedClock(_T0)
    tokens = (f"resend-{n}" for n in count())
    return ResendVerificationUseCase(
        users=users,
        verification_links=links,
        mailer=mailer,  # type: ignore[arg-type]
        clock=clock,
        token_factory=lambda: next(tokens),
    )


def test_resend_for_unverified_user_issues_fresh_link_and_mail() -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _RecordingMailer()
    users.add(_unverified())
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    result = use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    assert result == ACCEPTED
    # Exactly one fresh single-use link, expiring 24 h after issuance (Req 7.3).
    (link,) = links.by_token.values()
    assert link.user_stable_id == "user-0"
    assert link.issued_at == _T0
    assert link.expires_at == _T0 + VERIFICATION_LINK_TTL
    assert timedelta(hours=24) == VERIFICATION_LINK_TTL
    assert not link.is_consumed
    # Exactly one mail, carrying the new token to the learner.
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == "learner@example.com"
    assert mailer.sent[0].headers["X-Verification-Token"] == link.token


def test_resend_for_verified_user_changes_nothing_but_returns_accepted() -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _RecordingMailer()
    users.add(replace(_unverified(), status=USER_STATUS_VERIFIED))
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    result = use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    # Non-disclosure: identical accepted result, but no link and no mail for an
    # already-verified account (Req 24.3, 7.6-style).
    assert result == ACCEPTED
    assert not links.by_token
    assert not mailer.sent


def test_resend_for_unknown_email_changes_nothing_but_returns_accepted() -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _RecordingMailer()
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    result = use_case.execute(ResendVerificationRequest(email="stranger@example.com"))

    # Same accepted result for an unregistered email — account existence is not
    # disclosed (Req 7.6-style).
    assert result == ACCEPTED
    assert not links.by_token
    assert not mailer.sent


def test_resend_mail_failure_keeps_user_unverified_and_raises() -> None:
    users, links = _FakeUsers(), _FakeLinks()
    users.add(_unverified())
    use_case = _make_use_case(users=users, links=links, mailer=_FailingMailer())

    with pytest.raises(ResendVerificationError):
        use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    # The account is still there and still unverified (Req 24.1).
    still = users.get_by_email("learner@example.com")
    assert still is not None
    assert still.status == USER_STATUS_UNVERIFIED


def test_resend_mail_failure_does_not_leak_provider_error() -> None:
    users, links = _FakeUsers(), _FakeLinks()
    users.add(_unverified())
    secret_text = "smtp: 535 auth failed for user admin@internal.example"
    use_case = _make_use_case(
        users=users, links=links, mailer=_FailingMailer(RuntimeError(secret_text))
    )

    with pytest.raises(ResendVerificationError) as excinfo:
        use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    assert secret_text not in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert str(excinfo.value.__cause__) == secret_text


def test_resend_mail_failure_logs_safe_structured_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    users, links = _FakeUsers(), _FakeLinks()
    users.add(_unverified())
    use_case = _make_use_case(users=users, links=links, mailer=_FailingMailer())

    with (
        caplog.at_level(logging.WARNING, logger="edu_it_application.resend_verification"),
        pytest.raises(ResendVerificationError),
    ):
        use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    (record,) = [r for r in caplog.records if r.name == "edu_it_application.resend_verification"]
    fields = record.__dict__
    assert fields["event"] == "resend_verification_email_delivery_failed"
    assert fields["user_stable_id"] == "user-0"
    assert "learner@example.com" not in record.getMessage()
    assert "smtp" not in record.getMessage().lower()


def test_resend_is_repeatable_issuing_a_new_link_each_time() -> None:
    users, links, mailer = _FakeUsers(), _FakeLinks(), _RecordingMailer()
    users.add(_unverified())
    use_case = _make_use_case(users=users, links=links, mailer=mailer)

    use_case.execute(ResendVerificationRequest(email="learner@example.com"))
    use_case.execute(ResendVerificationRequest(email="learner@example.com"))

    # Re-requesting is permitted repeatedly, each time minting a distinct link
    # and mail (Req 24.3).
    assert len(links.by_token) == 2
    assert len(mailer.sent) == 2
    assert len({mail.headers["X-Verification-Token"] for mail in mailer.sent}) == 2
