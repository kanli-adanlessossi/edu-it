"""Unit tests for the password-reset use cases (task 15.1).

Requirements 9.1-9.6, 24.2, 24.3, 25.3, 28.1, 28.2.

These exercise the two use cases in
:mod:`edu_it_application.reset_password` directly against small, self-contained
in-test doubles for the domain ports — the application layer depends only on the
domain, so the tests do too (they do not import the infrastructure fakes),
mirroring ``test_authenticate`` and ``test_verify_email``. The doubles implement
exactly the port methods the use cases call and let each example assert the
observable outcome: whether a reset mail was sent, the link's TTL as measured by
the injected Clock, whether the stored password changed, which Sessions survive,
and the non-disclosing result.

Covered behavior:

- A request for a registered email stores one single-use reset link expiring 1 h
  after issuance per the Clock and sends one reset mail (Req 9.1).
- A request for an unregistered email stores nothing and sends nothing while
  returning the identical non-disclosing result (Req 9.6).
- A ``Mailer`` failure during a request changes no password, logs, and raises so
  the learner can re-request (Req 24.2, 24.3).
- Completion on a valid, unexpired, unused link with a policy-compliant password
  updates the password, marks the link used, revokes every prior Session, and
  establishes exactly one new Session (Req 9.2, 9.5, 28.1, 28.2).
- A stale link (expired, used, or unrecognized) leaves the password unchanged and
  offers to restart (Req 9.3, 25.3).
- A weak password leaves the password and the link unchanged and reports the
  policy failure (Req 9.4).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from edu_it_application.reset_password import (
    ACCEPTED,
    CompletePasswordResetRequest,
    CompletePasswordResetUseCase,
    PasswordResetMailDeliveryError,
    RequestPasswordResetRequest,
    RequestPasswordResetUseCase,
    ResetCompletionOutcome,
)
from edu_it_domain.config import RESET_LINK_TTL, SESSION_DURATION
from edu_it_domain.ports.mailer import OutboundMail
from edu_it_domain.ports.repository import SessionRecord, UserRecord
from edu_it_domain.ports.reset_link import ResetLink
from edu_it_domain.ports.session_store import ResolvedSession

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_USER_ID = "user-1"
_EMAIL = "learner@example.com"
_TOKEN = "reset-token-1"
_NEW_PASSWORD = "correct horse battery staple"  # 28 chars: within 12-128
_NEW_REF = "new-session-ref"


class _MutableClock:
    """A minimal :class:`Clock` double whose instant can be set in tests."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class _FakeUsers:
    """A minimal :class:`UserRepository` double keyed by email / stable_id."""

    def __init__(self) -> None:
        self.by_stable_id: dict[str, UserRecord] = {}

    def add(self, user: UserRecord) -> None:
        self.by_stable_id[user.stable_id] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:  # pragma: no cover
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self.by_stable_id.values() if u.email == email), None)

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover
        return False


class _FakeResetLinks:
    """A minimal :class:`ResetLinkRepository` double over a dict of links."""

    def __init__(self) -> None:
        self.by_token: dict[str, ResetLink] = {}

    def add(self, link: ResetLink) -> None:
        if link.token in self.by_token:
            raise ValueError("duplicate reset link")
        self.by_token[link.token] = link

    def get(self, token: str) -> ResetLink | None:
        return self.by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        link = self.by_token.get(token)
        if link is None or link.is_consumed:
            return False
        self.by_token[token] = replace(link, consumed_at=consumed_at)
        return True


class _FakeCredentials:
    """A minimal :class:`CredentialStore` double over a dict of hashed secrets."""

    def __init__(self) -> None:
        self.by_user: dict[str, str] = {}

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        self.by_user[user_stable_id] = hashed_secret

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        stored = self.by_user.get(user_stable_id)
        return stored is not None and stored == f"hash:{password}"


class _FakeSessions:
    """A minimal :class:`SessionRepository` double over a dict of durable records."""

    def __init__(self) -> None:
        self.by_ref: dict[str, SessionRecord] = {}

    def add(self, session: SessionRecord) -> None:
        if session.server_side_ref in self.by_ref:
            raise ValueError("duplicate session")
        self.by_ref[session.server_side_ref] = session

    def get(self, server_side_ref: str) -> SessionRecord | None:
        return self.by_ref.get(server_side_ref)

    def revoke(self, server_side_ref: str, *, revoked_at: datetime) -> bool:  # pragma: no cover
        session = self.by_ref.get(server_side_ref)
        if session is None or session.is_revoked:
            return False
        self.by_ref[server_side_ref] = replace(session, revoked_at=revoked_at)
        return True

    def revoke_all_for_user(self, user_stable_id: str, *, revoked_at: datetime) -> int:
        revoked_count = 0
        for ref, session in list(self.by_ref.items()):
            if session.user_stable_id == user_stable_id and not session.is_revoked:
                self.by_ref[ref] = replace(session, revoked_at=revoked_at)
                revoked_count += 1
        return revoked_count


class _FakeSessionStore:
    """A minimal :class:`SessionStore` double: Clock-driven expiry, revoke flag."""

    def __init__(self, clock: _MutableClock) -> None:
        self._clock = clock
        self.entries: dict[str, dict[str, object]] = {}

    def create(self, server_side_ref: str, user_stable_id: str, *, expires_at: datetime) -> None:
        self.entries[server_side_ref] = {
            "user_stable_id": user_stable_id,
            "expires_at": expires_at,
            "revoked": False,
        }

    def resolve(self, server_side_ref: str) -> ResolvedSession | None:
        entry = self.entries.get(server_side_ref)
        if entry is None or entry["revoked"]:
            return None
        expires_at = entry["expires_at"]
        assert isinstance(expires_at, datetime)
        if self._clock.now() >= expires_at:
            return None
        user_stable_id = entry["user_stable_id"]
        assert isinstance(user_stable_id, str)
        return ResolvedSession(
            server_side_ref=server_side_ref,
            user_stable_id=user_stable_id,
            expires_at=expires_at,
        )

    def revoke(self, server_side_ref: str) -> bool:  # pragma: no cover
        if self.resolve(server_side_ref) is None:
            return False
        self.entries[server_side_ref]["revoked"] = True
        return True

    def revoke_all_for_user(self, user_stable_id: str) -> int:
        revoked_count = 0
        for ref in list(self.entries):
            if self.resolve(ref) is None:
                continue
            if self.entries[ref]["user_stable_id"] == user_stable_id:
                self.entries[ref]["revoked"] = True
                revoked_count += 1
        return revoked_count


class _RecordingMailer:
    """A :class:`Mailer` double that retains sent mail (or always fails)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[OutboundMail] = []
        self._fail = fail

    def send(self, mail: OutboundMail) -> None:
        if self._fail:
            raise RuntimeError("smtp unavailable")
        self.sent.append(mail)


def _hash(password: str) -> str:
    """The test hashing scheme the credential double verifies against."""
    return f"hash:{password}"


# --- Request: enqueue a reset email for a registered email (Req 9.1) ----------


def _request_setup(
    *, registered: bool = True, fail_mail: bool = False
) -> tuple[RequestPasswordResetUseCase, _FakeResetLinks, _RecordingMailer, _MutableClock]:
    users = _FakeUsers()
    if registered:
        users.add(UserRecord(stable_id=_USER_ID, email=_EMAIL, locale="en", status="verified"))
    reset_links = _FakeResetLinks()
    mailer = _RecordingMailer(fail=fail_mail)
    clock = _MutableClock(_T0)
    use_case = RequestPasswordResetUseCase(
        users=users,
        reset_links=reset_links,
        mailer=mailer,
        clock=clock,
        token_factory=lambda: _TOKEN,
    )
    return use_case, reset_links, mailer, clock


def test_registered_email_enqueues_one_reset_link_and_mail() -> None:
    use_case, reset_links, mailer, clock = _request_setup()

    result = use_case.execute(RequestPasswordResetRequest(email=_EMAIL))

    assert result is ACCEPTED
    # Exactly one single-use link stored, expiring 1 h after issuance (Req 9.1).
    assert set(reset_links.by_token) == {_TOKEN}
    link = reset_links.by_token[_TOKEN]
    assert link.user_stable_id == _USER_ID
    assert link.issued_at == clock.now()
    assert link.expires_at == clock.now() + RESET_LINK_TTL
    assert link.expires_at - link.issued_at == timedelta(hours=1)
    # Exactly one reset mail carrying the token.
    assert len(mailer.sent) == 1
    assert mailer.sent[0].to == _EMAIL
    assert mailer.sent[0].headers.get("X-Reset-Token") == _TOKEN


def test_unregistered_email_enqueues_nothing_and_returns_identical_result() -> None:
    # Req 9.6: no mail, no link, and the byte-for-byte identical result, so
    # account existence is not disclosed.
    use_case, reset_links, mailer, _ = _request_setup(registered=False)

    result = use_case.execute(RequestPasswordResetRequest(email="nobody@example.com"))

    assert result is ACCEPTED
    assert not reset_links.by_token
    assert not mailer.sent


def test_request_results_are_identical_for_registered_and_unregistered() -> None:
    registered, _, _, _ = _request_setup(registered=True)
    unregistered, _, _, _ = _request_setup(registered=False)

    r1 = registered.execute(RequestPasswordResetRequest(email=_EMAIL))
    r2 = unregistered.execute(RequestPasswordResetRequest(email="nobody@example.com"))

    assert r1 == r2


def test_mail_failure_raises_and_changes_no_password() -> None:
    # Req 24.2, 24.3: a Mailer failure makes no password change and surfaces an
    # error the learner can retry past. The link was stored before the send.
    use_case, reset_links, _, _ = _request_setup(fail_mail=True)

    with pytest.raises(PasswordResetMailDeliveryError):
        use_case.execute(RequestPasswordResetRequest(email=_EMAIL))

    # The link exists (stored before the send) so a retry is a plain re-request;
    # no password was ever touched by the request path.
    assert set(reset_links.by_token) == {_TOKEN}


# --- Completion: valid link + policy-compliant password (Req 9.2, 9.5) --------


def _completion_setup(
    *,
    issued_at: datetime = _T0,
    consumed_at: datetime | None = None,
    store_link: bool = True,
    prior_session_refs: tuple[str, ...] = ("old-a", "old-b"),
) -> tuple[
    CompletePasswordResetUseCase,
    _FakeResetLinks,
    _FakeCredentials,
    _FakeSessions,
    _FakeSessionStore,
    _MutableClock,
]:
    clock = _MutableClock(_T0)
    reset_links = _FakeResetLinks()
    if store_link:
        reset_links.add(
            ResetLink(
                token=_TOKEN,
                user_stable_id=_USER_ID,
                issued_at=issued_at,
                expires_at=issued_at + RESET_LINK_TTL,
                consumed_at=consumed_at,
            )
        )
    credentials = _FakeCredentials()
    credentials.set_password(_USER_ID, _hash("old password that is long enough"))
    sessions = _FakeSessions()
    session_store = _FakeSessionStore(clock)
    for ref in prior_session_refs:
        expires = _T0 + SESSION_DURATION
        sessions.add(
            SessionRecord(server_side_ref=ref, user_stable_id=_USER_ID, expires_at=expires)
        )
        session_store.create(ref, _USER_ID, expires_at=expires)

    # A completed reset establishes exactly one Session, so the factory is called
    # at most once per execute; a fresh ref per mint keeps add() collision-free.
    refs = count()
    use_case = CompletePasswordResetUseCase(
        reset_links=reset_links,
        credentials=credentials,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        password_hasher=_hash,
        session_ref_factory=lambda: _NEW_REF if next(refs) == 0 else f"{_NEW_REF}-{next(refs)}",
    )
    return use_case, reset_links, credentials, sessions, session_store, clock


def test_valid_link_and_strong_password_completes_the_reset() -> None:
    use_case, reset_links, credentials, _, _, clock = _completion_setup()

    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD)
    )

    assert result.outcome is ResetCompletionOutcome.COMPLETED
    assert result.is_completed
    assert result.user_stable_id == _USER_ID
    assert result.server_side_ref == _NEW_REF
    # The password was updated to the new secret (Req 9.2).
    assert credentials.verify_password(_USER_ID, _NEW_PASSWORD)
    # The link is marked used (Req 9.2, 25.1).
    assert reset_links.by_token[_TOKEN].is_consumed
    assert reset_links.by_token[_TOKEN].consumed_at == clock.now()


def test_completion_revokes_all_prior_sessions_and_establishes_exactly_one() -> None:
    # Req 9.5, 28.1, 28.2: every prior Session is revoked (durable + ephemeral)
    # and exactly one new Session is established; afterward only it resolves.
    use_case, _, _, sessions, session_store, clock = _completion_setup(
        prior_session_refs=("old-a", "old-b", "old-c")
    )

    use_case.execute(CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD))

    # Durable records: every prior session revoked, the new one live.
    for ref in ("old-a", "old-b", "old-c"):
        prior = sessions.get(ref)
        assert prior is not None and prior.is_revoked
    new_durable = sessions.get(_NEW_REF)
    assert new_durable is not None and not new_durable.is_revoked
    assert new_durable.expires_at == clock.now() + SESSION_DURATION

    # Ephemeral store: exactly one reference resolves, and it is the new one.
    resolvable = [ref for ref in session_store.entries if session_store.resolve(ref) is not None]
    assert resolvable == [_NEW_REF]


def test_prior_session_revoked_by_reset_no_longer_authenticates() -> None:
    # Req 28.1: a Session revoked by a completed reset resolves to nothing and is
    # never refreshed or reissued.
    use_case, _, _, _, session_store, _ = _completion_setup(prior_session_refs=("old-a",))

    assert session_store.resolve("old-a") is not None
    use_case.execute(CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD))
    assert session_store.resolve("old-a") is None


def test_completion_succeeds_with_no_prior_sessions() -> None:
    use_case, _, credentials, sessions, session_store, _ = _completion_setup(prior_session_refs=())

    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD)
    )

    assert result.is_completed
    assert credentials.verify_password(_USER_ID, _NEW_PASSWORD)
    # Exactly one session now exists.
    assert set(sessions.by_ref) == {_NEW_REF}
    assert [r for r in session_store.entries if session_store.resolve(r)] == [_NEW_REF]


# --- Completion: stale link leaves state unchanged (Req 9.3, 25.3) ------------


def _assert_unchanged_invalid(
    use_case: CompletePasswordResetUseCase,
    credentials: _FakeCredentials,
    sessions: _FakeSessions,
    prior_refs: tuple[str, ...],
) -> None:
    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD)
    )
    assert result.outcome is ResetCompletionOutcome.INVALID_OR_EXPIRED
    assert result.offer_restart
    assert result.server_side_ref is None
    assert result.user_stable_id is None
    # Password unchanged (still the old secret), no new session established.
    assert not credentials.verify_password(_USER_ID, _NEW_PASSWORD)
    for ref in prior_refs:
        prior = sessions.get(ref)
        assert prior is not None and not prior.is_revoked
    assert _NEW_REF not in sessions.by_ref


def test_expired_link_is_rejected_and_changes_no_state() -> None:
    use_case, _, credentials, sessions, _, clock = _completion_setup()
    clock.set(_T0 + RESET_LINK_TTL)  # at expiry, link is invalid (strict)
    _assert_unchanged_invalid(use_case, credentials, sessions, ("old-a", "old-b"))


def test_already_used_link_is_rejected_and_changes_no_state() -> None:
    use_case, _, credentials, sessions, _, _ = _completion_setup(consumed_at=_T0)
    _assert_unchanged_invalid(use_case, credentials, sessions, ("old-a", "old-b"))


def test_unrecognized_link_is_rejected_and_changes_no_state() -> None:
    use_case, reset_links, credentials, sessions, _, _ = _completion_setup(store_link=False)
    _assert_unchanged_invalid(use_case, credentials, sessions, ("old-a", "old-b"))
    assert not reset_links.by_token


def test_single_use_second_completion_is_rejected() -> None:
    use_case, _, credentials, _sessions, _, _ = _completion_setup(prior_session_refs=())

    first = use_case.execute(CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD))
    second = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password="another valid passphrase!!")
    )

    assert first.outcome is ResetCompletionOutcome.COMPLETED
    assert second.outcome is ResetCompletionOutcome.INVALID_OR_EXPIRED
    # The second completion did not re-change the password to the other value.
    assert credentials.verify_password(_USER_ID, _NEW_PASSWORD)


def test_link_just_before_expiry_still_completes() -> None:
    use_case, _, credentials, _, _, clock = _completion_setup()
    clock.set(_T0 + RESET_LINK_TTL - timedelta(seconds=1))

    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD)
    )

    assert result.is_completed
    assert credentials.verify_password(_USER_ID, _NEW_PASSWORD)


# --- Completion: weak password leaves state unchanged (Req 9.4) ---------------


def test_weak_password_is_rejected_and_changes_no_state() -> None:
    use_case, reset_links, credentials, sessions, _, _ = _completion_setup()

    result = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password="short")  # < 12 chars
    )

    assert result.outcome is ResetCompletionOutcome.WEAK_PASSWORD
    assert not result.is_completed
    assert not result.offer_restart
    assert result.server_side_ref is None
    # Password unchanged and the link remains unused, so a retry is possible (Req 9.4).
    assert not credentials.verify_password(_USER_ID, "short")
    assert not reset_links.by_token[_TOKEN].is_consumed
    for ref in ("old-a", "old-b"):
        prior = sessions.get(ref)
        assert prior is not None and not prior.is_revoked
    assert _NEW_REF not in sessions.by_ref


def test_weak_password_check_runs_before_link_consumption_so_retry_works() -> None:
    use_case, reset_links, credentials, _, _, _ = _completion_setup(prior_session_refs=())

    weak = use_case.execute(CompletePasswordResetRequest(token=_TOKEN, new_password="short"))
    assert weak.outcome is ResetCompletionOutcome.WEAK_PASSWORD

    # The same link still works with a strong password (it was never consumed).
    strong = use_case.execute(
        CompletePasswordResetRequest(token=_TOKEN, new_password=_NEW_PASSWORD)
    )
    assert strong.is_completed
    assert credentials.verify_password(_USER_ID, _NEW_PASSWORD)
    assert reset_links.by_token[_TOKEN].is_consumed
