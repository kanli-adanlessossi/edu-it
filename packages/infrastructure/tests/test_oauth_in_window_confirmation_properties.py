"""Property-based test for in-window GitHub OAuth link confirmation (task 16.2).

Feature: foundations, Property 13: GitHub OAuth links an existing user only on explicit in-window confirmation

Property 13 (design.md): *For any* completed GitHub OAuth flow whose returned
verified email matches an existing User, the GitHub ``oauth_link`` is created if
and only if that User explicitly confirms the link before the issuance instant
plus the configured confirmation window as measured by the Clock; declining
(which invalidates the pending token immediately) or letting the window expire
creates no link and leaves the existing User's records unchanged, and while
confirmation is pending the temporary session grants no authenticated access to
the existing User.

This drives the OAuth use cases
(:class:`edu_it_application.oauth_sign_in.CompleteOAuthCallbackUseCase`,
:class:`~edu_it_application.oauth_sign_in.ConfirmOAuthLinkUseCase`,
:class:`~edu_it_application.oauth_sign_in.DeclineOAuthLinkUseCase`) directly,
wired to the infrastructure port fakes
(:class:`In_Memory_UserRepository`, :class:`In_Memory_OAuthLinkRepository`,
:class:`In_Memory_PendingOAuthLinkRepository`, :class:`In_Memory_SessionRepository`,
:class:`In_Memory_SessionStore`, :class:`Test_Clock`) exactly as the test/dev
composition root wires them — no database, Redis, or real clock. Both the
infrastructure fakes and the application use cases are imported, so the test
lives beside the other property tests that cross those two layers.

Each drawn example registers exactly one existing, verified User for a
well-formed email, then runs a completed, verified callback for that email — the
pending-confirmation path (Req 10.2). It asserts the temporary session is
unauthenticated (resolves to nothing, no durable record) and that no link exists
yet (Req 10.7). It then draws one of four mutually exclusive dispositions and
drives the confirm/decline step at a drawn instant, asserting the biconditional
the property names:

1. ``confirm_in_window`` — confirm strictly before the window closes. The link
   is created exactly once and an authenticated Session resolves for the existing
   User (Req 10.2, 26.2).
2. ``confirm_after_window`` — confirm at or after the window closes. No link is
   created and the existing User's records are unchanged (Req 10.6, 26.5).
3. ``decline`` — decline (invalidating the token immediately), then a later
   in-window confirm attempt. No link is ever created (Req 10.6, 26.4).
4. ``ignore`` — never act; the pending token simply sits. No link exists (Req
   10.6).

The link is created on exactly the one disposition where the User confirmed
in-window, and on no other; that biconditional, together with the
temporary-session-grants-nothing invariant, is the property.

Validates: Requirements 10.2, 10.6, 10.7, 26.2, 26.4, 26.5

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count

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
from edu_it_domain.config import OAUTH_LINK_CONFIRMATION_WINDOW, SESSION_DURATION
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB
from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, UserRecord
from edu_it_infrastructure.fakes import (
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    In_Memory_UserRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_EXISTING_USER_ID = "user-existing"

# --- Generators constrained to the property's input space --------------------

# A well-formed email that both registers the existing User and is returned,
# verified, by the callback — the same minimal accepted shape the other property
# tests draw (non-empty local part, one "@", a two-label domain).
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

# The GitHub stable subject for the returning identity; any non-empty token.
_subjects = st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=24)

# The locale the existing account is stored with; irrelevant to the property.
_locales = st.sampled_from(["fr", "en"])

# How far after issuance the confirm/decline step is attempted. The in-window
# dispositions stay strictly inside [0, window); the after-window one lands at or
# beyond it. The window is 15 minutes (900 s); draw seconds across a range that
# straddles the boundary so both sides are exercised densely.
_window_seconds = int(OAUTH_LINK_CONFIRMATION_WINDOW.total_seconds())
_in_window_offsets = st.integers(min_value=0, max_value=_window_seconds - 1)
_after_window_offsets = st.integers(min_value=_window_seconds, max_value=_window_seconds * 3)

# The four mutually exclusive dispositions the biconditional partitions over.
_dispositions = st.sampled_from(
    [
        "confirm_in_window",
        "confirm_after_window",
        "decline",
        "ignore",
    ]
)


def _make_use_cases() -> tuple[
    CompleteOAuthCallbackUseCase,
    ConfirmOAuthLinkUseCase,
    DeclineOAuthLinkUseCase,
    In_Memory_UserRepository,
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the three OAuth use cases to fresh infrastructure fakes.

    Mirrors the test/dev composition root: shared stores and a single
    controllable :class:`Test_Clock`, with deterministic factories for the user
    stable_id, the pending-confirmation token, and the session references. The
    reference factory mints a fresh value per call so the temporary session ref
    and any authenticated-session ref never collide in the fakes.
    """
    users = In_Memory_UserRepository()
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)

    refs = (f"session-ref-{n}" for n in count())
    complete = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: "user-new",
        pending_token_factory=lambda: "pending-token-0",
        session_ref_factory=lambda: next(refs),
    )
    confirm = ConfirmOAuthLinkUseCase(
        pending_links=pending_links,
        oauth_links=oauth_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        session_ref_factory=lambda: next(refs),
    )
    decline = DeclineOAuthLinkUseCase(pending_links=pending_links, clock=clock)
    return (
        complete,
        confirm,
        decline,
        users,
        oauth_links,
        pending_links,
        sessions,
        session_store,
        clock,
    )


@given(
    email=_emails,
    subject=_subjects,
    locale=_locales,
    disposition=_dispositions,
    in_window_offset=_in_window_offsets,
    after_window_offset=_after_window_offsets,
)
@settings(max_examples=200)
def test_property_13_oauth_links_existing_user_only_on_in_window_confirmation(
    email: str,
    subject: str,
    locale: str,
    disposition: str,
    in_window_offset: int,
    after_window_offset: int,
) -> None:
    """Feature: foundations, Property 13: GitHub OAuth links an existing user only on explicit in-window confirmation.

    For any completed, verified OAuth flow matching an existing User, the
    ``oauth_link`` is created iff that User confirms before issuance + the window
    (per the Clock); decline (which invalidates immediately) or expiry creates no
    link and leaves the User's records unchanged, and the pending temporary
    session grants no authenticated access (Req 10.2, 10.6, 10.7, 26.2, 26.4,
    26.5).
    """
    (
        complete,
        confirm,
        decline,
        users,
        oauth_links,
        _pending_links,
        sessions,
        session_store,
        clock,
    ) = _make_use_cases()

    # One existing, verified User whose email the verified callback will match.
    users.add(
        UserRecord(
            stable_id=_EXISTING_USER_ID,
            email=email,
            locale=locale,
            status=USER_STATUS_VERIFIED,
        )
    )

    # A completed, state-matched, verified callback for that very email: the
    # pending-confirmation path (Req 10.2). Issuance is measured at _T0.
    callback = OAuthCallback(
        completed=True,
        state_matches=True,
        subject=subject,
        email=email,
        email_verified=True,
    )
    pending_result = complete.execute(callback)

    # The flow is pending — no link, no authenticated access yet (Req 10.2, 10.7).
    assert pending_result.status is OAuthCallbackStatus.PENDING_CONFIRMATION
    assert pending_result.is_pending_confirmation
    token = pending_result.pending_token
    temp_ref = pending_result.temporary_session_ref
    assert token is not None
    assert temp_ref is not None

    # The temporary session grants no authenticated access to the existing User:
    # it is never written to either store, so it resolves to nothing (Req 10.7).
    assert session_store.resolve(temp_ref) is None
    assert sessions.get(temp_ref) is None
    # No oauth_link exists while confirmation is pending (Req 10.2).
    assert len(oauth_links) == 0

    # Drive the disposition and compute whether an in-window confirmation occurs.
    link_expected = False
    if disposition == "confirm_in_window":
        clock.set(_T0 + timedelta(seconds=in_window_offset))
        result = confirm.execute(ConfirmOAuthLinkRequest(token=token))
        link_expected = True
        assert result.outcome is OAuthLinkConfirmationOutcome.LINKED
    elif disposition == "confirm_after_window":
        # At or beyond the window close: confirmation is too late (Req 26.5).
        clock.set(_T0 + timedelta(seconds=after_window_offset))
        result = confirm.execute(ConfirmOAuthLinkRequest(token=token))
        assert result.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    elif disposition == "decline":
        # Decline invalidates the token immediately; a later in-window confirm
        # must still link nothing (Req 10.6, 26.4).
        declined = decline.execute(DeclineOAuthLinkRequest(token=token))
        assert declined.invalidated
        clock.set(_T0 + timedelta(seconds=in_window_offset))
        result = confirm.execute(ConfirmOAuthLinkRequest(token=token))
        assert result.outcome is OAuthLinkConfirmationOutcome.EXPIRED_OR_INVALID
    else:  # "ignore": never act on the pending token.
        clock.set(_T0 + timedelta(seconds=in_window_offset))

    # The biconditional: a link exists iff the User confirmed in-window.
    if link_expected:
        # Exactly one link, for this identity, owned by the existing User (Req 10.2).
        assert len(oauth_links) == 1
        link = oauth_links.get(PROVIDER_GITHUB, subject)
        assert link is not None
        assert link.user_stable_id == _EXISTING_USER_ID
        # An authenticated Session now resolves for the existing User, expiring at
        # now + SESSION_DURATION measured by the Clock (Req 10.2, 26.2).
        assert result.server_side_ref is not None
        durable = sessions.get(result.server_side_ref)
        assert durable is not None
        assert durable.user_stable_id == _EXISTING_USER_ID
        assert durable.expires_at == clock.now() + SESSION_DURATION
        resolved = session_store.resolve(result.server_side_ref)
        assert resolved is not None
        assert resolved.user_stable_id == _EXISTING_USER_ID
    else:
        # No link was created and the existing User's records are unchanged
        # (Req 10.6, 26.4, 26.5): the single registered user, no link, and no
        # Session at all — the only ref ever minted on this path is the temporary
        # one, which was never stored and so grants no authenticated access.
        assert len(oauth_links) == 0
        assert oauth_links.get(PROVIDER_GITHUB, subject) is None
        assert len(users) == 1
        assert users.get_by_stable_id(_EXISTING_USER_ID) is not None
        assert sessions.get(temp_ref) is None
        assert session_store.resolve(temp_ref) is None
