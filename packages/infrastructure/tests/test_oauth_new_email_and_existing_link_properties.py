"""Property-based test for OAuth new-email creation and existing-link sign-in (task 16.3).

Feature: foundations, Property 14: New verified GitHub email creates a single linked account

Property 14 (design.md): *For any* completed GitHub OAuth flow whose returned
verified email matches no existing User, a single User, its GitHub ``oauth_link``,
and a Session are created; and for any flow whose identity already has an
``oauth_link``, a Session is established for the linked User with no additional
account created.

This drives :class:`edu_it_application.oauth_sign_in.CompleteOAuthCallbackUseCase`
directly, wired to the infrastructure port fakes
(:class:`In_Memory_UserRepository`, :class:`In_Memory_OAuthLinkRepository`,
:class:`In_Memory_PendingOAuthLinkRepository`, :class:`In_Memory_SessionRepository`,
:class:`In_Memory_SessionStore`, :class:`Test_Clock`) and the injected
stable-id / pending-token / session-reference factories, exactly as the test/dev
composition root wires them — no database, Redis, or real clock. Both the
infrastructure fakes and the application use case are imported, so the test lives
beside the other property tests that cross those two layers.

Each drawn example starts from an empty set of stores, optionally seeds one
existing linked account, then draws one of the two mutually exclusive scenarios
the property partitions over:

1. ``new_email`` — a verified GitHub email that matches no existing User and
   whose identity has no ``oauth_link``. Exactly one User is created (verified),
   exactly one ``oauth_link`` records the identity, and exactly one Session is
   established and resolves for the new User (Req 10.1).
2. ``existing_link`` — a returning identity whose ``(provider, subject)`` already
   has an ``oauth_link``. The linked User is signed in with no second User and no
   second link created, and the established Session resolves for that same linked
   User (Req 10.4).

Across both scenarios the counts the property names hold exactly: the ``new_email``
path grows users and links each by one and establishes one Session; the
``existing_link`` path grows neither users nor links and establishes one Session
for the already-linked User.

Validates: Requirements 10.1, 10.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from edu_it_application.oauth_sign_in import (
    CompleteOAuthCallbackUseCase,
    OAuthCallback,
    OAuthCallbackStatus,
)
from edu_it_domain.config import SESSION_DURATION
from edu_it_domain.ports.oauth_link import PROVIDER_GITHUB, OAuthLink
from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    UserRecord,
)
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

# --- Generators constrained to the property's input space --------------------

# A well-formed email: the minimal accepted shape the other account property
# tests draw — a non-empty local part, one "@", and a two-label domain. The
# account is seeded (or looked up) directly here, so only a stable, well-shaped
# address matters.
_EMAIL_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_emails = st.builds(
    lambda local, label, tld: f"{local}@{label}.{tld}",
    local=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    label=st.text(alphabet=_EMAIL_ALPHABET, min_size=1, max_size=20),
    tld=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=6),
)

# GitHub's stable account identifier for the returning / new visitor. Any
# non-empty opaque string; the identity is keyed by (provider, subject).
_subjects = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=24)

# A locale the seeded account is stored with; irrelevant to the property but part
# of a UserRecord.
_locales = st.sampled_from(["fr", "en"])

# The two mutually exclusive scenarios the property partitions over.
_scenarios = st.sampled_from(["new_email", "existing_link"])


def _make_use_case() -> tuple[
    CompleteOAuthCallbackUseCase,
    In_Memory_UserRepository,
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
]:
    """Wire the OAuth callback use case to fresh infrastructure fakes.

    Mirrors the test/dev composition root: deterministic stable-id,
    pending-token, and session-reference factories so the whole flow is
    reproducible under Hypothesis, with empty stores so the drawn/seeded account
    is the only one present.
    """
    users = In_Memory_UserRepository()
    oauth_links = In_Memory_OAuthLinkRepository()
    pending_links = In_Memory_PendingOAuthLinkRepository()
    sessions = In_Memory_SessionRepository()
    clock = Test_Clock(start=_T0)
    session_store = In_Memory_SessionStore(clock)

    ids = (f"user-{n}" for n in count())
    tokens = (f"pending-{n}" for n in count())
    refs = (f"ref-{n}" for n in count())

    use_case = CompleteOAuthCallbackUseCase(
        users=users,
        oauth_links=oauth_links,
        pending_links=pending_links,
        sessions=sessions,
        session_store=session_store,
        clock=clock,
        stable_id_factory=lambda: next(ids),
        pending_token_factory=lambda: next(tokens),
        session_ref_factory=lambda: next(refs),
    )
    return use_case, users, oauth_links, pending_links, sessions, session_store, clock


@given(
    email=_emails,
    subject=_subjects,
    locale=_locales,
    scenario=_scenarios,
)
@settings(max_examples=200)
def test_property_14_new_email_creates_single_linked_account_existing_link_signs_in(
    email: str,
    subject: str,
    locale: str,
    scenario: str,
) -> None:
    """Feature: foundations, Property 14: New verified GitHub email creates a single linked account.

    For any completed, verified GitHub OAuth flow: a new email matching no User
    creates exactly one User, one ``oauth_link``, and one resolvable Session (Req
    10.1); a flow whose identity already has an ``oauth_link`` signs in the linked
    User with no additional User or link created (Req 10.4).
    """
    use_case, users, oauth_links, pending_links, sessions, session_store, clock = _make_use_case()

    existing_user_id = "user-existing"
    if scenario == "existing_link":
        # Seed exactly one already-linked verified account for the returning
        # identity. The callback email may differ from the stored one; the
        # (provider, subject) link is authoritative (Req 10.4).
        users.add(
            UserRecord(
                stable_id=existing_user_id,
                email=email,
                locale=locale,
                status=USER_STATUS_VERIFIED,
            )
        )
        oauth_links.add(
            OAuthLink(
                provider=PROVIDER_GITHUB,
                subject=subject,
                user_stable_id=existing_user_id,
            )
        )

    users_before = len(users)
    links_before = len(oauth_links)

    callback = OAuthCallback(
        completed=True,
        state_matches=True,
        subject=subject,
        email=email,
        email_verified=True,
    )
    before = clock.now()
    result = use_case.execute(callback)

    # Both branches sign the visitor in — no confirmation is pending — and leave
    # no pending record behind.
    assert result.status is OAuthCallbackStatus.SIGNED_IN
    assert result.is_signed_in
    assert result.server_side_ref is not None
    assert result.user_stable_id is not None
    assert result.pending_token is None

    # Exactly one Session was established: a durable record owning the signed-in
    # user and expiring at now + SESSION_DURATION per the Clock, plus ephemeral
    # state that resolves to that same user (Req 10.1, 10.4, ADR 0004).
    durable = sessions.get(result.server_side_ref)
    assert durable is not None
    assert durable.user_stable_id == result.user_stable_id
    assert durable.expires_at == before + SESSION_DURATION
    assert not durable.is_revoked
    resolved = session_store.resolve(result.server_side_ref)
    assert resolved is not None
    assert resolved.user_stable_id == result.user_stable_id

    # The identity's link always names the signed-in user.
    link = oauth_links.get(PROVIDER_GITHUB, subject)
    assert link is not None
    assert link.user_stable_id == result.user_stable_id

    if scenario == "new_email":
        # A single new User and a single new oauth_link were created (Req 10.1).
        assert len(users) == users_before + 1
        assert len(oauth_links) == links_before + 1
        created = users.get_by_stable_id(result.user_stable_id)
        assert created is not None
        # GitHub already verified the email, so the OAuth account is verified.
        assert created.email == email
        assert created.status == USER_STATUS_VERIFIED
    else:
        # The existing link signed in the already-linked User with no duplicate:
        # no new User and no new link were created (Req 10.4).
        assert result.user_stable_id == existing_user_id
        assert len(users) == users_before
        assert len(oauth_links) == links_before

    # Neither branch leaves a pending-confirmation record behind.
    assert pending_links.get(result.server_side_ref) is None
