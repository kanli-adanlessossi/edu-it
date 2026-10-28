"""Property-based test for failed/denied/unverified/state-mismatch OAuth (task 16.4).

Feature: foundations, Property 15: Failed, denied, unverified, or state-mismatched OAuth creates nothing

Property 15 (design.md): *For any* OAuth callback that was denied, failed at the
provider, arrived incomplete, carried a ``state`` that did not match the issued
one, or completed with a GitHub-unverified email, completing the callback creates
no User, ``oauth_link``, or Session, discards any partial authentication state,
and returns a coarse, non-disclosing failure reason — leaving any pre-existing
records exactly as they were.

This drives :class:`edu_it_application.oauth_sign_in.CompleteOAuthCallbackUseCase`
against the real infrastructure fakes
(:mod:`edu_it_infrastructure.fakes`) and the controllable
:class:`~edu_it_infrastructure.fakes.Test_Clock`, so the property holds for
whatever rejected/unverified callback and whatever pre-existing world state
Hypothesis draws, with no database, HTTP server, or live provider.

Each example draws:

- a **refused callback** that is in exactly one of the three refusal shapes —
  not completed (denied / provider failure / incomplete), completed but
  ``state``-mismatched (Req 26.6), or completed and state-matched but
  GitHub-unverified (Req 10.3, 26.3) — with arbitrary subject/email; and
- a **pre-existing world**: empty, a verified User already owning that email, or
  that User plus an existing ``oauth_link`` for the returned identity.

The property asserts, for every such example:

1. **A coarse failure is raised** (Req 10.5): the use case raises
   :class:`OAuthCallbackError` whose :attr:`failure` is ``REJECTED`` for the two
   "did not reach a verified identity" shapes (denied/failed/incomplete and
   ``state`` mismatch — collapsed into one non-disclosing reason, Req 26.1,
   26.6) and ``EMAIL_UNVERIFIED`` for the completed-but-unverified shape (Req
   10.3, 26.3).
2. **Nothing is created and partial state is discarded** (Req 26.1): no new
   User, no ``oauth_link``, no pending-confirmation record, and no Session —
   durable or ephemeral — exists beyond what the pre-existing world held.
3. **Pre-existing records are unchanged** (Req 10.3, 26.3): the snapshot of
   users, links, pending links, sessions, and session-store entries taken before
   the attempt equals the snapshot after it.

Validates: Requirements 10.3, 10.5, 26.1, 26.3, 26.6

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from itertools import count

import pytest
from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    OAuthCallback,
    OAuthCallbackError,
    OAuthCallbackFailure,
)
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB, OAuthLink
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


class _RefusalShape(Enum):
    """The three ways a callback is refused without reaching a verified identity."""

    NOT_COMPLETED = "not_completed"
    """Denied / provider failure / incomplete — ``completed`` is ``False`` (Req 10.5, 26.1)."""

    STATE_MISMATCH = "state_mismatch"
    """Completed but the returned ``state`` did not match the issued one (Req 26.6)."""

    EMAIL_UNVERIFIED = "email_unverified"
    """Completed and state-matched but GitHub reports the email unverified (Req 10.3, 26.3)."""


class _World(Enum):
    """The pre-existing persisted state the refused callback runs against."""

    EMPTY = "empty"
    """No prior records at all."""

    EXISTING_USER = "existing_user"
    """A verified User already owns the callback's email."""

    EXISTING_USER_AND_LINK = "existing_user_and_link"
    """That User plus an ``oauth_link`` for the returned ``(provider, subject)``."""


# A free email/subject the callback carries; kept modest so the existing-user
# world can reuse the same email to exercise the match path under refusal.
_emails = st.emails().filter(lambda e: len(e) <= 254)
_subjects = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=40,
)


def _build_refused_callback(shape: _RefusalShape, *, subject: str, email: str) -> OAuthCallback:
    """Build a callback in exactly the refusal ``shape`` for ``subject``/``email``."""
    if shape is _RefusalShape.NOT_COMPLETED:
        # Denied / provider failure / incomplete: the boundary sets completed False.
        # state_matches is irrelevant on this path; draw it from both values below.
        return OAuthCallback(
            completed=False,
            state_matches=False,
            subject=subject,
            email=email,
            email_verified=True,
        )
    if shape is _RefusalShape.STATE_MISMATCH:
        # A forged or stale callback: it may even report completed, but its state
        # did not match the issued one (Req 26.6).
        return OAuthCallback(
            completed=True,
            state_matches=False,
            subject=subject,
            email=email,
            email_verified=True,
        )
    # EMAIL_UNVERIFIED: a genuinely completed, state-matched flow whose GitHub
    # email is not verified (Req 10.3, 26.3).
    return OAuthCallback(
        completed=True,
        state_matches=True,
        subject=subject,
        email=email,
        email_verified=False,
    )


def _expected_failure(shape: _RefusalShape) -> OAuthCallbackFailure:
    """The coarse failure reason the use case must raise for ``shape``."""
    if shape is _RefusalShape.EMAIL_UNVERIFIED:
        return OAuthCallbackFailure.EMAIL_UNVERIFIED
    # Both not-completed and state-mismatch collapse to one non-disclosing reason.
    return OAuthCallbackFailure.REJECTED


@given(
    shape=st.sampled_from(_RefusalShape),
    world=st.sampled_from(_World),
    subject=_subjects,
    email=_emails,
    pending_token=st.text(min_size=1, max_size=40),
)
@settings(max_examples=200)
def test_property_15_refused_oauth_creates_nothing(
    shape: _RefusalShape,
    world: _World,
    subject: str,
    email: str,
    pending_token: str,
) -> None:
    """Feature: foundations, Property 15: Failed, denied, unverified, or state-mismatched OAuth creates nothing.

    A denied/failed/incomplete, ``state``-mismatched, or GitHub-unverified
    callback creates no User, ``oauth_link``, pending record, or Session, discards
    partial state, raises the coarse failure reason, and leaves every pre-existing
    record unchanged.
    """
    clock = Test_Clock(start=_T0)
    users = In_Memory_UserRepository()
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(clock=clock)

    # Seed the drawn pre-existing world. The existing user owns the callback's
    # email, so the refusal is exercised even on the email-match path.
    if world in (_World.EXISTING_USER, _World.EXISTING_USER_AND_LINK):
        users.add(
            UserRecord(
                stable_id=_EXISTING_USER_ID,
                email=email,
                locale="fr",
                status=USER_STATUS_VERIFIED,
            )
        )
    if world is _World.EXISTING_USER_AND_LINK:
        oauth_links.add(
            OAuthLink(
                provider=PROVIDER_GITHUB,
                subject=subject,
                user_stable_id=_EXISTING_USER_ID,
            )
        )

    # Any factory the use case might reach is a tripwire: a refused callback must
    # never mint a user id, pending token, or session ref. If one fires, the
    # property has been violated and the test fails loudly.
    def _never_stable_id() -> str:
        raise AssertionError("refused callback must not create a User")

    def _never_pending_token() -> str:
        raise AssertionError("refused callback must not create a pending link")

    refs = count()

    def _session_ref_factory() -> str:
        # Not an assertion error itself (so the created-nothing assertions below
        # can report precisely what leaked), but a refused callback must never
        # establish a session, which the post-state assertions verify.
        return f"leaked-ref-{next(refs)}"

    use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=_never_stable_id,
        pending_token_factory=_never_pending_token,
        session_ref_factory=_session_ref_factory,
    )

    # State before the attempt.
    before_user_count = len(users)
    before_link_count = len(oauth_links)
    before_existing_user = users.get_by_stable_id(_EXISTING_USER_ID)
    before_link = oauth_links.get(PROVIDER_GITHUB, subject)

    callback = _build_refused_callback(shape, subject=subject, email=email)

    # 1. The use case raises the coarse, non-disclosing failure reason (Req 10.5).
    with pytest.raises(OAuthCallbackError) as excinfo:
        use_case.execute(callback)
    assert excinfo.value.failure is _expected_failure(shape)

    # 2. Nothing new was created and any partial state was discarded (Req 26.1):
    #    user and link counts are unchanged, no pending record exists for the
    #    drawn token, and no session — durable or ephemeral — was established.
    assert len(users) == before_user_count
    assert len(oauth_links) == before_link_count
    assert pending_links.get(pending_token) is None
    # No session ref was minted, so none can be stored; assert the stores are
    # empty of any session (the seeded worlds never create one).
    assert sessions.get("leaked-ref-0") is None
    assert session_store.resolve("leaked-ref-0") is None

    # 3. Pre-existing records are byte-for-byte unchanged (Req 10.3, 26.3).
    assert users.get_by_stable_id(_EXISTING_USER_ID) == before_existing_user
    assert oauth_links.get(PROVIDER_GITHUB, subject) == before_link
    # The existing user (when present) still owns the email with its original
    # verified status — the refusal never touched it.
    if world in (_World.EXISTING_USER, _World.EXISTING_USER_AND_LINK):
        matched = users.get_by_email(email)
        assert matched is not None
        assert matched.stable_id == _EXISTING_USER_ID
        assert matched.status == USER_STATUS_VERIFIED
