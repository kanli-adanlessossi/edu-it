"""Unit tests for the in-memory OAuth-link / pending-link fakes (task 16.1, Req 5.2).

These prove the two new in-memory fakes satisfy their domain ports and enforce
the same invariants their production counterparts do, and that the OAuth sign-in
use cases (task 16.1) run unchanged against them — the substitutability the fakes
exist for (Req 5.2). The use cases here are wired to the real infrastructure
fakes (not in-test doubles) so the application-layer flow and the infrastructure
fakes are exercised together.
"""

from __future__ import annotations

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
    OAuthCallbackStatus,
    OAuthLinkConfirmationOutcome,
)
from edu_it_domain.config import OAUTH_LINK_CONFIRMATION_WINDOW
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB, OAuthLink, OAuthLinkRepository
from edu_it_domain.ports.pending_oauth_link import (
    PendingOAuthLink,
    PendingOAuthLinkRepository,
)
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_UserRepository,
    Test_Clock,
)

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_SUBJECT = "gh-subject-1"
_EMAIL = "learner@example.com"


def test_in_memory_oauth_link_repository_rejects_duplicate_identity() -> None:
    links: OAuthLinkRepository = In_Memory_OAuthLinkRepository()
    link = OAuthLink(provider=PROVIDER_GITHUB, subject=_SUBJECT, user_stable_id="u1")

    links.add(link)
    assert links.get(PROVIDER_GITHUB, _SUBJECT) == link
    assert links.get(PROVIDER_GITHUB, "other") is None

    # One external identity maps to at most one User (Req 10.4).
    with pytest.raises(ValueError, match="identity"):
        links.add(OAuthLink(provider=PROVIDER_GITHUB, subject=_SUBJECT, user_stable_id="u2"))


def test_in_memory_pending_oauth_link_repository_enforces_single_use() -> None:
    pending_links: PendingOAuthLinkRepository = In_Memory_PendingOAuthLinkRepository()
    pending = PendingOAuthLink(
        token="tok",
        provider=PROVIDER_GITHUB,
        subject=_SUBJECT,
        user_stable_id="u1",
        email=_EMAIL,
        issued_at=_T0,
        expires_at=_T0 + OAUTH_LINK_CONFIRMATION_WINDOW,
    )

    pending_links.add(pending)
    assert pending_links.get("tok") == pending
    assert pending_links.get("unknown") is None

    with pytest.raises(ValueError, match="token"):
        pending_links.add(pending)

    # First consumption (confirm or decline) succeeds; a second is rejected (Req 26.4).
    assert pending_links.mark_consumed("tok", consumed_at=_T0) is True
    assert pending_links.mark_consumed("tok", consumed_at=_T0) is False
    assert pending_links.mark_consumed("unknown", consumed_at=_T0) is False
    consumed = pending_links.get("tok")
    assert consumed is not None and consumed.is_consumed


def _callback(**overrides: object) -> OAuthCallback:
    base = {
        "completed": True,
        "state_matches": True,
        "subject": _SUBJECT,
        "email": _EMAIL,
        "email_verified": True,
    }
    base.update(overrides)
    return OAuthCallback(**base)  # type: ignore[arg-type]


def test_oauth_callback_and_confirmation_run_against_the_infrastructure_fakes() -> None:
    # A verified email matching an existing User begins pending confirmation; the
    # visitor then confirms and is signed in — end to end against the real fakes.
    users = In_Memory_UserRepository()
    users.add(UserRecord(stable_id="u1", email=_EMAIL, locale="fr", status=USER_STATUS_VERIFIED))
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock=clock)
    refs = (f"ref-{n}" for n in count())

    callback_use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: "unused",
        pending_token_factory=lambda: "pending-tok",
        session_ref_factory=lambda: next(refs),
    )
    confirm_use_case = ConfirmOAuthLinkUseCase(
        pending_links=pending_links,
        oauth_links=oauth_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        session_ref_factory=lambda: next(refs),
    )

    pending_result = callback_use_case.execute(_callback())
    assert pending_result.status is OAuthCallbackStatus.PENDING_CONFIRMATION
    assert pending_result.temporary_session_ref is not None
    # The temporary session is unauthenticated: it is never stored.
    assert session_store.resolve(pending_result.temporary_session_ref) is None
    assert not oauth_links.get(PROVIDER_GITHUB, _SUBJECT)

    confirmed = confirm_use_case.execute(
        ConfirmOAuthLinkRequest(token=pending_result.pending_token)  # type: ignore[arg-type]
    )
    assert confirmed.outcome is OAuthLinkConfirmationOutcome.LINKED
    assert confirmed.user_stable_id == "u1"
    # The link is now made and an authenticated session resolves.
    link = oauth_links.get(PROVIDER_GITHUB, _SUBJECT)
    assert link is not None and link.user_stable_id == "u1"
    resolved = session_store.resolve(confirmed.server_side_ref)  # type: ignore[arg-type]
    assert resolved is not None and resolved.user_stable_id == "u1"


def test_oauth_callback_new_email_creates_linked_account_against_fakes() -> None:
    users = In_Memory_UserRepository()
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock=clock)
    refs = (f"ref-{n}" for n in count())

    callback_use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: "new-user",
        pending_token_factory=lambda: "unused",
        session_ref_factory=lambda: next(refs),
    )

    result = callback_use_case.execute(_callback())

    assert result.status is OAuthCallbackStatus.SIGNED_IN
    assert result.user_stable_id == "new-user"
    assert len(users) == 1
    created = users.get_by_email(_EMAIL)
    assert created is not None and created.status == USER_STATUS_VERIFIED
    link = oauth_links.get(PROVIDER_GITHUB, _SUBJECT)
    assert link is not None and link.user_stable_id == "new-user"
    resolved = session_store.resolve(result.server_side_ref)  # type: ignore[arg-type]
    assert resolved is not None and resolved.user_stable_id == "new-user"


def test_decline_against_fakes_blocks_later_confirmation() -> None:
    users = In_Memory_UserRepository()
    users.add(UserRecord(stable_id="u1", email=_EMAIL, locale="fr", status=USER_STATUS_VERIFIED))
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock=clock)
    refs = (f"ref-{n}" for n in count())

    callback_use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: "unused",
        pending_token_factory=lambda: "pending-tok",
        session_ref_factory=lambda: next(refs),
    )
    confirm_use_case = ConfirmOAuthLinkUseCase(
        pending_links=pending_links,
        oauth_links=oauth_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        session_ref_factory=lambda: next(refs),
    )
    decline_use_case = DeclineOAuthLinkUseCase(pending_links=pending_links, clock=clock)

    pending_result = callback_use_case.execute(_callback())
    declined = decline_use_case.execute(
        DeclineOAuthLinkRequest(token=pending_result.pending_token)  # type: ignore[arg-type]
    )
    assert declined.invalidated

    after = confirm_use_case.execute(
        ConfirmOAuthLinkRequest(token=pending_result.pending_token)  # type: ignore[arg-type]
    )
    assert after.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    assert not oauth_links.get(PROVIDER_GITHUB, _SUBJECT)
