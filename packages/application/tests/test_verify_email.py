"""Unit tests for the verification-link consumption use case (task 12.2, Req 7.4, 7.5, 25.1, 25.2).

These exercise :class:`edu_it_application.verify_email.VerifyEmailUseCase`
directly against small, self-contained in-test doubles for the domain ports —
the application layer depends only on the domain, so the tests do too (they do
not import the infrastructure fakes). The doubles implement exactly the port
methods the use case calls and let each example assert the observable outcome:
the returned result, the user's verification status, and whether the link was
consumed.

Covered behavior:

- A valid, unexpired, unused link marks the user verified and invalidates the
  link (Req 7.4, 25.1).
- An expired, already-used, or unrecognized link leaves verification state
  unchanged and returns an invalid/expired result offering to resend (Req 7.5,
  25.2).
- Single use: a second opening of a link that already succeeded does not
  re-verify and reports invalid/expired (Req 7.4, 25.1).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.verify_email import (
    VerifyEmailOutcome,
    VerifyEmailRequest,
    VerifyEmailUseCase,
)
from edu_it_domain.config import VERIFICATION_LINK_TTL
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    UserRecord,
)
from edu_it_domain.ports.verification_link import VerificationLink

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_USER_ID = "user-1"
_TOKEN = "token-1"


class _MutableClock:
    """A minimal :class:`Clock` double whose instant can be advanced in tests."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class _FakeUsers:
    """A minimal :class:`UserRepository` double keyed by ``stable_id``."""

    def __init__(self) -> None:
        self.by_stable_id: dict[str, UserRecord] = {}

    def add(self, user: UserRecord) -> None:
        self.by_stable_id[user.stable_id] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:  # pragma: no cover
        return next((u for u in self.by_stable_id.values() if u.email == email), None)

    def mark_verified(self, stable_id: str) -> bool:
        user = self.by_stable_id.get(stable_id)
        if user is None or user.status != USER_STATUS_UNVERIFIED:
            return False
        self.by_stable_id[stable_id] = replace(user, status=USER_STATUS_VERIFIED)
        return True


class _FakeLinks:
    """A minimal :class:`VerificationLinkRepository` double over a dict."""

    def __init__(self) -> None:
        self.by_token: dict[str, VerificationLink] = {}

    def add(self, link: VerificationLink) -> None:
        self.by_token[link.token] = link

    def get(self, token: str) -> VerificationLink | None:
        return self.by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        link = self.by_token.get(token)
        if link is None or link.is_consumed:
            return False
        self.by_token[token] = replace(link, consumed_at=consumed_at)
        return True


def _setup(
    *,
    status: str = USER_STATUS_UNVERIFIED,
    issued_at: datetime = _T0,
    consumed_at: datetime | None = None,
    store_link: bool = True,
) -> tuple[VerifyEmailUseCase, _FakeUsers, _FakeLinks, _MutableClock]:
    users = _FakeUsers()
    users.add(
        UserRecord(stable_id=_USER_ID, email="learner@example.com", locale="en", status=status)
    )
    links = _FakeLinks()
    if store_link:
        links.add(
            VerificationLink(
                token=_TOKEN,
                user_stable_id=_USER_ID,
                issued_at=issued_at,
                expires_at=issued_at + VERIFICATION_LINK_TTL,
                consumed_at=consumed_at,
            )
        )
    clock = _MutableClock(_T0)
    use_case = VerifyEmailUseCase(users=users, verification_links=links, clock=clock)
    return use_case, users, links, clock


def _status(users: _FakeUsers) -> str:
    """Return the test user's current status, asserting the user is present."""
    user = users.get_by_stable_id(_USER_ID)
    assert user is not None
    return user.status


def _link(links: _FakeLinks) -> VerificationLink:
    """Return the test link, asserting it is present."""
    link = links.get(_TOKEN)
    assert link is not None
    return link


def test_valid_unused_link_verifies_and_invalidates() -> None:
    use_case, users, links, _ = _setup()

    result = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    # The email is verified and the result reports success (Req 7.4).
    assert result.outcome is VerifyEmailOutcome.VERIFIED
    assert result.is_verified
    assert not result.offer_resend
    assert _status(users) == USER_STATUS_VERIFIED
    # The link is invalidated (consumed), so a second use is already used (Req 25.1).
    assert _link(links).is_consumed


def test_link_consumed_exactly_at_issuance_instant() -> None:
    # The link is openable strictly before expiry; opening it at issuance time
    # (well within the 24 h window) succeeds and stamps the consume instant.
    use_case, _, links, clock = _setup()

    use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert _link(links).consumed_at == clock.now()


def test_unrecognized_token_is_rejected_and_changes_no_state() -> None:
    use_case, users, links, _ = _setup(store_link=False)

    result = use_case.execute(VerifyEmailRequest(token="no-such-token"))

    assert result.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert result.offer_resend
    # Verification state unchanged; no link materialized (Req 7.5).
    assert _status(users) == USER_STATUS_UNVERIFIED
    assert not links.by_token


def test_expired_link_is_rejected_and_changes_no_state() -> None:
    use_case, users, links, clock = _setup()
    # Advance to exactly the expiry instant: the link expires *at* issuance + TTL
    # and is invalid at/after that instant (Req 7.3, 25.2).
    clock.set(_T0 + VERIFICATION_LINK_TTL)

    result = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert result.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert result.offer_resend
    assert _status(users) == USER_STATUS_UNVERIFIED
    # An expired link is never consumed by a rejected attempt.
    assert not _link(links).is_consumed


def test_already_used_link_is_rejected_and_changes_no_state() -> None:
    # A link already consumed before the attempt is invalid/expired (Req 7.4, 25.2).
    use_case, users, _, _ = _setup(status=USER_STATUS_VERIFIED, consumed_at=_T0)

    result = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert result.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert result.offer_resend
    # The already-verified user stays verified; nothing is re-changed.
    assert _status(users) == USER_STATUS_VERIFIED


def test_single_use_second_open_does_not_reverify() -> None:
    use_case, users, links, _ = _setup()

    first = use_case.execute(VerifyEmailRequest(token=_TOKEN))
    second = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert first.outcome is VerifyEmailOutcome.VERIFIED
    # The second opening lands on the invalid/expired path (single use, Req 7.4, 25.1).
    assert second.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert second.offer_resend
    assert _status(users) == USER_STATUS_VERIFIED
    assert _link(links).is_consumed


def test_link_just_before_expiry_still_verifies() -> None:
    use_case, users, _, clock = _setup()
    # One second before expiry the link is still valid (strict inequality).
    clock.set(_T0 + VERIFICATION_LINK_TTL - timedelta(seconds=1))

    result = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert result.outcome is VerifyEmailOutcome.VERIFIED
    assert _status(users) == USER_STATUS_VERIFIED


@pytest.mark.parametrize("offset", [timedelta(seconds=1), timedelta(hours=1)])
def test_link_after_expiry_is_rejected(offset: timedelta) -> None:
    use_case, users, _, clock = _setup()
    clock.set(_T0 + VERIFICATION_LINK_TTL + offset)

    result = use_case.execute(VerifyEmailRequest(token=_TOKEN))

    assert result.outcome is VerifyEmailOutcome.INVALID_OR_EXPIRED
    assert _status(users) == USER_STATUS_UNVERIFIED
