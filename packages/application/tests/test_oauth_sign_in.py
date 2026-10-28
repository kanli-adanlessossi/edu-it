"""Unit tests for the GitHub OAuth callback flow and linking state machine (task 16.1).

Requirements 10.1-10.7, 26.1-26.6.

These exercise the three use cases in :mod:`edu_it_application.oauth_sign_in`
directly against small, self-contained in-test doubles for the domain ports —
the application layer depends only on the domain, so the tests do too (they do
not import the infrastructure fakes), mirroring ``test_authenticate`` and
``test_reset_password``. The doubles implement exactly the port methods the use
cases call and let each example assert the observable outcome: which records were
created, whether a Session was established and resolves, and the non-disclosing
refusal reason.

Covered behavior (the design's state machine):

- A verified email matching no User creates one User + one ``oauth_link`` +
  a Session (Req 10.1).
- An existing ``oauth_link`` signs in the linked User with no duplicate (Req 10.4).
- A verified email matching an existing User stores a single-use pending
  confirmation and a temporary *unauthenticated* session; no link yet (Req 10.2,
  10.7).
- Confirming within the window makes the link and establishes a Session (Req
  10.2, 26.2); declining invalidates the pending token immediately (Req 10.6,
  26.4); window expiry refuses the link and leaves records unchanged (Req 10.6,
  26.5).
- A denied/failed/incomplete flow, a GitHub-unverified email, or a ``state``
  mismatch creates nothing and raises a coarse, non-disclosing error (Req 10.3,
  10.5, 26.1, 26.3, 26.6).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count

import pytest
from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    ConfirmOAuthLinkRequest,
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkRequest,
    DeclineOAuthLinkUseCase,
    OAuthCallback,
    OAuthCallbackError,
    OAuthCallbackFailure,
    OAuthCallbackStatus,
    OAuthLinkConfirmationOutcome,
)
from edu_it_domain.config import OAUTH_LINK_CONFIRMATION_WINDOW, SESSION_DURATION
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB, OAuthLink
from edu_it_domain.ports.pending_oauth_link import PendingOAuthLink
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    SessionRecord,
    UserRecord,
)
from edu_it_domain.ports.session_store import ResolvedSession

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_SUBJECT = "gh-subject-1"
_EMAIL = "learner@example.com"
_EXISTING_USER_ID = "user-existing"
_NEW_USER_ID = "user-new"
_PENDING_TOKEN = "pending-token-1"
_NEW_REF = "session-ref-new"


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
        if user.stable_id in self.by_stable_id:
            raise ValueError("duplicate user")
        if any(u.email == user.email for u in self.by_stable_id.values()):
            raise ValueError("duplicate email")
        self.by_stable_id[user.stable_id] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:  # pragma: no cover
        return self.by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        return next((u for u in self.by_stable_id.values() if u.email == email), None)

    def mark_verified(self, stable_id: str) -> bool:  # pragma: no cover
        return False


class _FakeOAuthLinks:
    """A minimal :class:`OAuthLinkRepository` double keyed by (provider, subject)."""

    def __init__(self) -> None:
        self.by_identity: dict[tuple[str, str], OAuthLink] = {}

    def add(self, link: OAuthLink) -> None:
        key = (link.provider, link.subject)
        if key in self.by_identity:
            raise ValueError("duplicate oauth link")
        self.by_identity[key] = link

    def get(self, provider: str, subject: str) -> OAuthLink | None:
        return self.by_identity.get((provider, subject))


class _FakePendingLinks:
    """A minimal :class:`PendingOAuthLinkRepository` double over a dict of records."""

    def __init__(self) -> None:
        self.by_token: dict[str, PendingOAuthLink] = {}

    def add(self, pending: PendingOAuthLink) -> None:
        if pending.token in self.by_token:
            raise ValueError("duplicate pending link")
        self.by_token[pending.token] = pending

    def get(self, token: str) -> PendingOAuthLink | None:
        return self.by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        pending = self.by_token.get(token)
        if pending is None or pending.is_consumed:
            return False
        self.by_token[token] = replace(pending, consumed_at=consumed_at)
        return True


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

    def revoke_all_for_user(  # pragma: no cover
        self, user_stable_id: str, *, revoked_at: datetime
    ) -> int:
        return 0


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

    def revoke_all_for_user(self, user_stable_id: str) -> int:  # pragma: no cover
        return 0


# --- Builders ----------------------------------------------------------------


def _callback(
    *,
    completed: bool = True,
    state_matches: bool = True,
    email_verified: bool = True,
    email: str = _EMAIL,
    subject: str = _SUBJECT,
) -> OAuthCallback:
    return OAuthCallback(
        completed=completed,
        state_matches=state_matches,
        subject=subject,
        email=email,
        email_verified=email_verified,
    )


def _callback_setup(
    *, with_existing_user: bool = False, with_existing_link: bool = False
) -> tuple[
    CompleteOAuthCallbackUseCase,
    _FakeUsers,
    _FakeOAuthLinks,
    _FakePendingLinks,
    _FakeSessions,
    _FakeSessionStore,
    _MutableClock,
]:
    clock = _MutableClock(_T0)
    users = _FakeUsers()
    oauth_links = _FakeOAuthLinks()
    pending_links = _FakePendingLinks()
    sessions = _FakeSessions()
    session_store = _FakeSessionStore(clock)

    if with_existing_user:
        users.add(
            UserRecord(
                stable_id=_EXISTING_USER_ID,
                email=_EMAIL,
                locale="fr",
                status=USER_STATUS_VERIFIED,
            )
        )
    if with_existing_link:
        oauth_links.add(
            OAuthLink(provider=PROVIDER_GITHUB, subject=_SUBJECT, user_stable_id=_EXISTING_USER_ID)
        )

    refs = count()

    def session_ref_factory() -> str:
        n = next(refs)
        return _NEW_REF if n == 0 else f"{_NEW_REF}-{n}"

    use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: _NEW_USER_ID,
        pending_token_factory=lambda: _PENDING_TOKEN,
        session_ref_factory=session_ref_factory,
    )
    return use_case, users, oauth_links, pending_links, sessions, session_store, clock


# --- No match: create one User + one oauth_link + a Session (Req 10.1) --------


def test_new_verified_email_creates_user_link_and_session() -> None:
    use_case, users, oauth_links, _, sessions, session_store, clock = _callback_setup()

    result = use_case.execute(_callback())

    assert result.status is OAuthCallbackStatus.SIGNED_IN
    assert result.is_signed_in
    assert result.user_stable_id == _NEW_USER_ID
    assert result.server_side_ref == _NEW_REF
    # Exactly one User, created verified (GitHub already verified the email).
    assert set(users.by_stable_id) == {_NEW_USER_ID}
    created = users.by_stable_id[_NEW_USER_ID]
    assert created.email == _EMAIL
    assert created.status == USER_STATUS_VERIFIED
    # Exactly one oauth_link for the identity.
    link = oauth_links.get(PROVIDER_GITHUB, _SUBJECT)
    assert link is not None and link.user_stable_id == _NEW_USER_ID
    # A resolvable (authenticated) Session was established, expiring per the Clock.
    durable = sessions.get(_NEW_REF)
    assert durable is not None and durable.expires_at == clock.now() + SESSION_DURATION
    resolved = session_store.resolve(_NEW_REF)
    assert resolved is not None and resolved.user_stable_id == _NEW_USER_ID


# --- Existing link: sign in the linked User, no duplicate (Req 10.4) ----------


def test_existing_link_signs_in_linked_user_without_duplicate() -> None:
    use_case, users, oauth_links, _, _, session_store, _ = _callback_setup(
        with_existing_user=True, with_existing_link=True
    )

    result = use_case.execute(_callback())

    assert result.status is OAuthCallbackStatus.SIGNED_IN
    assert result.user_stable_id == _EXISTING_USER_ID
    # No duplicate User and no duplicate link were created.
    assert set(users.by_stable_id) == {_EXISTING_USER_ID}
    assert set(oauth_links.by_identity) == {(PROVIDER_GITHUB, _SUBJECT)}
    # The established Session authenticates the linked User.
    assert result.server_side_ref is not None
    resolved = session_store.resolve(result.server_side_ref)
    assert resolved is not None and resolved.user_stable_id == _EXISTING_USER_ID


def test_existing_link_signs_in_even_when_github_email_changed() -> None:
    # The (provider, subject) link is authoritative, not the email: a linked user
    # whose GitHub email later changed still signs in (Req 10.4).
    use_case, _, _, _, _, _, _ = _callback_setup(with_existing_user=True, with_existing_link=True)

    result = use_case.execute(_callback(email="changed@example.com"))

    assert result.status is OAuthCallbackStatus.SIGNED_IN
    assert result.user_stable_id == _EXISTING_USER_ID


# --- Email conflict: pending confirmation + temporary unauth session (Req 10.2, 10.7) ---


def test_verified_email_matching_existing_user_begins_pending_confirmation() -> None:
    use_case, users, oauth_links, pending_links, sessions, session_store, clock = _callback_setup(
        with_existing_user=True
    )

    result = use_case.execute(_callback())

    assert result.status is OAuthCallbackStatus.PENDING_CONFIRMATION
    assert result.is_pending_confirmation
    assert result.pending_token == _PENDING_TOKEN
    assert result.temporary_session_ref is not None
    # No link and no new user yet — only an explicit confirmation makes the link.
    assert not oauth_links.by_identity
    assert set(users.by_stable_id) == {_EXISTING_USER_ID}
    # The pending record names the identity and existing user, windowed by the Clock.
    pending = pending_links.by_token[_PENDING_TOKEN]
    assert pending.user_stable_id == _EXISTING_USER_ID
    assert pending.subject == _SUBJECT
    assert pending.expires_at == clock.now() + OAUTH_LINK_CONFIRMATION_WINDOW
    # The temporary session grants no authenticated access: it is never stored, so
    # it resolves to nothing (Req 10.7).
    assert session_store.resolve(result.temporary_session_ref) is None
    assert sessions.get(result.temporary_session_ref) is None
    assert not session_store.entries


# --- Rejected: denied / failed / incomplete / state mismatch (Req 10.5, 26.1, 26.6) ---


@pytest.mark.parametrize(
    "callback",
    [
        _callback(completed=False),  # denied / failed / incomplete
        _callback(state_matches=False),  # state mismatch (Req 26.6)
    ],
)
def test_rejected_callback_creates_nothing(callback: OAuthCallback) -> None:
    use_case, users, oauth_links, pending_links, sessions, session_store, _ = _callback_setup()

    with pytest.raises(OAuthCallbackError) as excinfo:
        use_case.execute(callback)

    assert excinfo.value.failure is OAuthCallbackFailure.REJECTED
    # No User, link, pending record, or Session — partial state discarded.
    assert not users.by_stable_id
    assert not oauth_links.by_identity
    assert not pending_links.by_token
    assert not sessions.by_ref
    assert not session_store.entries


def test_state_mismatch_rejected_even_when_flow_completed() -> None:
    # A completed flow with a forged/stale state is still rejected (Req 26.6).
    use_case, users, _, _, _, _, _ = _callback_setup()

    with pytest.raises(OAuthCallbackError) as excinfo:
        use_case.execute(_callback(completed=True, state_matches=False))

    assert excinfo.value.failure is OAuthCallbackFailure.REJECTED
    assert not users.by_stable_id


# --- Email unverified at GitHub: refuse, records unchanged (Req 10.3, 26.3) ---


def test_github_unverified_email_is_refused_and_changes_nothing() -> None:
    use_case, users, oauth_links, pending_links, _, _, _ = _callback_setup(with_existing_user=True)

    with pytest.raises(OAuthCallbackError) as excinfo:
        use_case.execute(_callback(email_verified=False))

    assert excinfo.value.failure is OAuthCallbackFailure.EMAIL_UNVERIFIED
    # The existing User is unchanged; no link and no pending record were created.
    assert set(users.by_stable_id) == {_EXISTING_USER_ID}
    assert not oauth_links.by_identity
    assert not pending_links.by_token


# --- Confirmation / decline of a pending link --------------------------------


def _confirm_decline_setup(
    *,
    issued_at: datetime = _T0,
    consumed_at: datetime | None = None,
    store_pending: bool = True,
) -> tuple[
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkUseCase,
    _FakePendingLinks,
    _FakeOAuthLinks,
    _FakeSessions,
    _FakeSessionStore,
    _MutableClock,
]:
    clock = _MutableClock(_T0)
    pending_links = _FakePendingLinks()
    if store_pending:
        pending_links.add(
            PendingOAuthLink(
                token=_PENDING_TOKEN,
                provider=PROVIDER_GITHUB,
                subject=_SUBJECT,
                user_stable_id=_EXISTING_USER_ID,
                email=_EMAIL,
                issued_at=issued_at,
                expires_at=issued_at + OAUTH_LINK_CONFIRMATION_WINDOW,
                consumed_at=consumed_at,
            )
        )
    oauth_links = _FakeOAuthLinks()
    sessions = _FakeSessions()
    session_store = _FakeSessionStore(clock)

    confirm = ConfirmOAuthLinkUseCase(
        pending_links=pending_links,
        oauth_links=oauth_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        session_ref_factory=lambda: _NEW_REF,
    )
    decline = DeclineOAuthLinkUseCase(pending_links=pending_links, clock=clock)
    return confirm, decline, pending_links, oauth_links, sessions, session_store, clock


def test_confirm_within_window_makes_link_and_signs_in() -> None:
    (confirm, _, pending_links, oauth_links, sessions, session_store, clock) = (
        _confirm_decline_setup()
    )

    result = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))

    assert result.outcome is OAuthLinkConfirmationOutcome.LINKED
    assert result.is_linked
    assert result.user_stable_id == _EXISTING_USER_ID
    assert result.server_side_ref == _NEW_REF
    # The oauth_link is now made for the existing user.
    link = oauth_links.get(PROVIDER_GITHUB, _SUBJECT)
    assert link is not None and link.user_stable_id == _EXISTING_USER_ID
    # The pending record is consumed (single use).
    assert pending_links.by_token[_PENDING_TOKEN].is_consumed
    # An authenticated Session resolves for the linked user.
    durable = sessions.get(_NEW_REF)
    assert durable is not None and durable.expires_at == clock.now() + SESSION_DURATION
    resolved = session_store.resolve(_NEW_REF)
    assert resolved is not None and resolved.user_stable_id == _EXISTING_USER_ID


def test_confirm_at_window_boundary_is_too_late() -> None:
    # The window closes exactly at issuance + window (strict), so confirming at
    # the boundary links nothing (Req 26.5).
    confirm, _, _, oauth_links, sessions, _, clock = _confirm_decline_setup()
    clock.set(_T0 + OAUTH_LINK_CONFIRMATION_WINDOW)

    result = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))

    assert result.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    assert not oauth_links.by_identity
    assert not sessions.by_ref


def test_confirm_expired_window_refuses_and_changes_nothing() -> None:
    confirm, _, pending_links, oauth_links, sessions, _, clock = _confirm_decline_setup()
    clock.set(_T0 + OAUTH_LINK_CONFIRMATION_WINDOW * 2)

    result = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))

    assert result.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    assert result.server_side_ref is None
    # Records unchanged: no link, no session; the pending record stays unconsumed.
    assert not oauth_links.by_identity
    assert not sessions.by_ref
    assert not pending_links.by_token[_PENDING_TOKEN].is_consumed


def test_confirm_unrecognized_token_is_refused() -> None:
    confirm, _, _, oauth_links, sessions, _, _ = _confirm_decline_setup(store_pending=False)

    result = confirm.execute(ConfirmOAuthLinkRequest(token="unknown"))

    assert result.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    assert not oauth_links.by_identity
    assert not sessions.by_ref


def test_confirm_is_single_use() -> None:
    confirm, _, _, oauth_links, sessions, _, _ = _confirm_decline_setup()

    first = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))
    second = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))

    assert first.outcome is OAuthLinkConfirmationOutcome.LINKED
    assert second.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    # Exactly one link and one session from the single successful confirmation.
    assert set(oauth_links.by_identity) == {(PROVIDER_GITHUB, _SUBJECT)}
    assert set(sessions.by_ref) == {_NEW_REF}


def test_decline_invalidates_pending_token_immediately() -> None:
    confirm, decline, pending_links, oauth_links, sessions, _, _ = _confirm_decline_setup()

    declined = decline.execute(DeclineOAuthLinkRequest(token=_PENDING_TOKEN))

    assert declined.invalidated
    # The token is consumed at once, so a later confirm links nothing (Req 26.4).
    assert pending_links.by_token[_PENDING_TOKEN].is_consumed
    after = confirm.execute(ConfirmOAuthLinkRequest(token=_PENDING_TOKEN))
    assert after.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    assert not oauth_links.by_identity
    assert not sessions.by_ref


def test_decline_is_idempotent_safe_no_op_on_unknown_token() -> None:
    _, decline, _, _, _, _, _ = _confirm_decline_setup(store_pending=False)

    result = decline.execute(DeclineOAuthLinkRequest(token="unknown"))

    # Declining an unknown token succeeds but invalidated nothing.
    assert not result.invalidated


def test_decline_then_decline_is_idempotent() -> None:
    _, decline, _, _, _, _, _ = _confirm_decline_setup()

    first = decline.execute(DeclineOAuthLinkRequest(token=_PENDING_TOKEN))
    second = decline.execute(DeclineOAuthLinkRequest(token=_PENDING_TOKEN))

    assert first.invalidated
    assert not second.invalidated
