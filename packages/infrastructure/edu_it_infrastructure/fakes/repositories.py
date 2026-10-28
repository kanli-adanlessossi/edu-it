"""In-memory identity / verification-link repository fakes (Req 5.2).

These fakes implement the domain persistence ports with plain in-process
dictionaries so the account use cases (sign-up in task 12.1, and the flows built
on it) can be exercised in tests and local development without a database:

- :class:`In_Memory_UserRepository` implements
  :class:`~edu_it_domain.ports.repository.UserRepository`.
- :class:`In_Memory_VerificationLinkRepository` implements
  :class:`~edu_it_domain.ports.verification_link.VerificationLinkRepository`.
- :class:`In_Memory_ResetLinkRepository` implements
  :class:`~edu_it_domain.ports.reset_link.ResetLinkRepository`.

Each fake is substitutable for its PostgreSQL / Redis-backed production
counterpart without changing domain or application code (Req 5.2): it satisfies
the same protocol and enforces the same invariants (unique ``stable_id`` and
email for users; unique token and single-use consumption for verification and
reset links). The fakes hold no internal numeric key — a user is addressed by
its ``stable_id``, a link by its opaque ``token`` — matching the domain's view.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.oauth_link import (
    OAuthLink,
    OAuthLinkRepository,
)
from edu_it_domain.ports.pending_oauth_link import (
    PendingOAuthLink,
    PendingOAuthLinkRepository,
)
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.reset_link import (
    ResetLink,
    ResetLinkRepository,
)
from edu_it_domain.ports.role_assignment import (
    RoleAssignment,
    RoleAssignmentRepository,
)
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)

__all__ = [
    "In_Memory_CredentialStore",
    "In_Memory_OAuthLinkRepository",
    "In_Memory_PendingOAuthLinkRepository",
    "In_Memory_ResetLinkRepository",
    "In_Memory_RoleAssignmentRepository",
    "In_Memory_SessionRepository",
    "In_Memory_UserRepository",
    "In_Memory_VerificationLinkRepository",
]


class In_Memory_UserRepository(UserRepository):
    """A :class:`UserRepository` backed by in-process dictionaries.

    Satisfies the :class:`~edu_it_domain.ports.repository.UserRepository`
    protocol, so the application layer persists and looks up users exactly as it
    does against the PostgreSQL adapter. Uniqueness of ``stable_id`` and email is
    enforced on :meth:`add`, so a duplicate account cannot be created.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty repository."""
        self._by_stable_id: dict[str, UserRecord] = {}
        self._by_email: dict[str, UserRecord] = {}

    def add(self, user: UserRecord) -> None:
        """Insert ``user``; reject a duplicate ``stable_id`` or email.

        Raises :class:`ValueError` when a user with the same ``stable_id`` or
        email already exists, matching the PostgreSQL adapter's contract.
        """
        if user.stable_id in self._by_stable_id:
            raise ValueError(f"user with stable_id {user.stable_id!r} already exists")
        if user.email in self._by_email:
            raise ValueError(f"user with email {user.email!r} already exists")
        self._by_stable_id[user.stable_id] = user
        self._by_email[user.email] = user

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:
        """Return the user with ``stable_id``, or ``None`` when absent."""
        return self._by_stable_id.get(stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        """Return the user with ``email``, or ``None`` when absent."""
        return self._by_email.get(email)

    def mark_verified(self, stable_id: str) -> bool:
        """Transition the user for ``stable_id`` from unverified to verified.

        Returns whether an unverified user was transitioned: ``False`` when no
        user exists for ``stable_id`` or the user is already verified, so the
        transition is idempotent and matches the PostgreSQL adapter's contract
        (Req 7.4, 7.5).
        """
        user = self._by_stable_id.get(stable_id)
        if user is None or user.status != USER_STATUS_UNVERIFIED:
            return False
        verified = replace(user, status=USER_STATUS_VERIFIED)
        self._by_stable_id[stable_id] = verified
        self._by_email[verified.email] = verified
        return True

    def __len__(self) -> int:
        """Return how many users are stored (for test assertions)."""
        return len(self._by_stable_id)


class In_Memory_VerificationLinkRepository(VerificationLinkRepository):
    """A :class:`VerificationLinkRepository` backed by an in-process dictionary.

    Satisfies the
    :class:`~edu_it_domain.ports.verification_link.VerificationLinkRepository`
    protocol. A token uniquely identifies a link (:meth:`add` rejects a reused
    token), and :meth:`mark_consumed` enforces single use idempotently, matching
    the Redis-backed production store.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty repository."""
        self._by_token: dict[str, VerificationLink] = {}

    def add(self, link: VerificationLink) -> None:
        """Store ``link``; reject a reused ``token``.

        Raises :class:`ValueError` when a link already exists for the token, so a
        token is never reused for two links.
        """
        if link.token in self._by_token:
            raise ValueError(f"verification link for token {link.token!r} already exists")
        self._by_token[link.token] = link

    def get(self, token: str) -> VerificationLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized."""
        return self._by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the link for ``token`` consumed at ``consumed_at``.

        Returns whether an unused link was consumed: ``False`` when the token is
        unknown or the link was already consumed, so consumption is idempotent
        and single use is enforced (Req 7.4).
        """
        link = self._by_token.get(token)
        if link is None or link.is_consumed:
            return False
        self._by_token[token] = VerificationLink(
            token=link.token,
            user_stable_id=link.user_stable_id,
            issued_at=link.issued_at,
            expires_at=link.expires_at,
            consumed_at=consumed_at,
        )
        return True


class In_Memory_ResetLinkRepository(ResetLinkRepository):
    """A :class:`ResetLinkRepository` backed by an in-process dictionary.

    Satisfies the :class:`~edu_it_domain.ports.reset_link.ResetLinkRepository`
    protocol. A token uniquely identifies a link (:meth:`add` rejects a reused
    token), and :meth:`mark_consumed` enforces single use idempotently, matching
    the Redis-backed production store so the password-reset use cases (task 15.1)
    run against it exactly as against production.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty repository."""
        self._by_token: dict[str, ResetLink] = {}

    def add(self, link: ResetLink) -> None:
        """Store ``link``; reject a reused ``token``.

        Raises :class:`ValueError` when a link already exists for the token, so a
        token is never reused for two links.
        """
        if link.token in self._by_token:
            raise ValueError(f"reset link for token {link.token!r} already exists")
        self._by_token[link.token] = link

    def get(self, token: str) -> ResetLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized."""
        return self._by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the link for ``token`` consumed at ``consumed_at``.

        Returns whether an unused link was consumed: ``False`` when the token is
        unknown or the link was already consumed, so consumption is idempotent
        and single use is enforced (Req 9.2).
        """
        link = self._by_token.get(token)
        if link is None or link.is_consumed:
            return False
        self._by_token[token] = ResetLink(
            token=link.token,
            user_stable_id=link.user_stable_id,
            issued_at=link.issued_at,
            expires_at=link.expires_at,
            consumed_at=consumed_at,
        )
        return True


class In_Memory_CredentialStore(CredentialStore):
    """A :class:`CredentialStore` backed by an in-process dictionary (Req 5.2).

    Satisfies the
    :class:`~edu_it_domain.ports.credential_store.CredentialStore` protocol, so
    the sign-in use case verifies passwords exactly as it would against the
    production store. The stored value is whatever already-hashed secret the
    caller supplies (the store performs no hashing of its own); verification is
    an equality check that the caller hashes the submitted password the same way
    it hashed the stored secret.

    Non-disclosure (Req 8.4) is preserved structurally: :meth:`verify_password`
    returns ``False`` both for a user with no stored credential and for a wrong
    password, drawing no distinction between the two, matching the production
    store's contract.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty credential store."""
        self._by_user: dict[str, str] = {}

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        """Store (or replace) the already-hashed credential for a user."""
        self._by_user[user_stable_id] = hashed_secret

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        """Return whether ``password`` matches the stored credential.

        ``False`` when no credential is stored for ``user_stable_id`` or the
        supplied secret does not equal the stored one, with no observable
        distinction between the two cases (Req 8.4).
        """
        stored = self._by_user.get(user_stable_id)
        if stored is None:
            return False
        return stored == password


class In_Memory_SessionRepository(SessionRepository):
    """A :class:`SessionRepository` backed by an in-process dictionary (Req 5.2).

    Satisfies the
    :class:`~edu_it_domain.ports.repository.SessionRepository` protocol, so the
    account use cases write and revoke the durable ``session`` record exactly as
    they do against the PostgreSQL adapter. The durable record is the identity
    system of record (ADR 0004); the ephemeral fast-path state lives behind the
    separate :class:`~edu_it_domain.ports.session_store.SessionStore`. A session
    is addressed by its opaque ``server_side_ref`` — no internal numeric key is
    held, matching the domain's view.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty session repository."""
        self._by_ref: dict[str, SessionRecord] = {}

    def add(self, session: SessionRecord) -> None:
        """Insert ``session``; reject a reused ``server_side_ref``.

        Raises :class:`ValueError` when a session already exists for the
        reference, matching the PostgreSQL adapter's contract.
        """
        if session.server_side_ref in self._by_ref:
            raise ValueError(
                f"session for server_side_ref {session.server_side_ref!r} already exists"
            )
        self._by_ref[session.server_side_ref] = session

    def get(self, server_side_ref: str) -> SessionRecord | None:
        """Return the session for ``server_side_ref``, or ``None`` when absent."""
        return self._by_ref.get(server_side_ref)

    def revoke(self, server_side_ref: str, *, revoked_at: datetime) -> bool:
        """Mark the session for ``server_side_ref`` revoked at ``revoked_at``.

        Returns whether a live session was revoked: ``False`` when no session
        exists for the reference or it was already revoked, so revocation is
        idempotent and sign-out stays a safe no-op (Req 8.2).
        """
        session = self._by_ref.get(server_side_ref)
        if session is None or session.is_revoked:
            return False
        self._by_ref[server_side_ref] = replace(session, revoked_at=revoked_at)
        return True

    def revoke_all_for_user(self, user_stable_id: str, *, revoked_at: datetime) -> int:
        """Revoke every live session owned by ``user_stable_id``.

        Returns the number of sessions newly revoked (live sessions only). Used
        by password reset (Req 9.5) and account deletion (Req 14.3).
        """
        revoked_count = 0
        for ref, session in list(self._by_ref.items()):
            if session.user_stable_id == user_stable_id and not session.is_revoked:
                self._by_ref[ref] = replace(session, revoked_at=revoked_at)
                revoked_count += 1
        return revoked_count


class In_Memory_RoleAssignmentRepository(RoleAssignmentRepository):
    """A :class:`RoleAssignmentRepository` backed by an in-process dictionary (Req 5.2).

    Satisfies the
    :class:`~edu_it_domain.ports.role_assignment.RoleAssignmentRepository`
    protocol, so the privileged-user CLI and the assign-role use case (task 18.1)
    grant roles exactly as they would against the PostgreSQL adapter. A user
    carries at most one role assignment, keyed by its ``stable_id``;
    :meth:`set_role` is last-write-wins so re-running the CLI for the same user
    updates rather than accumulates (Req 13.2). No internal numeric key is held —
    the owning user is referenced by its ``stable_id`` — matching the domain's
    view.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty role-assignment repository."""
        self._by_user: dict[str, RoleAssignment] = {}

    def set_role(self, assignment: RoleAssignment) -> None:
        """Grant ``assignment``'s role to its user, replacing any prior grant.

        Idempotent and last-write-wins: the user carries the single most recently
        assigned role, so re-running the CLI for the same user updates it in place
        (Req 13.2).
        """
        self._by_user[assignment.user_stable_id] = assignment

    def get_for_user(self, user_stable_id: str) -> RoleAssignment | None:
        """Return the role assignment for ``user_stable_id``, or ``None``."""
        return self._by_user.get(user_stable_id)

    def __len__(self) -> int:
        """Return how many role assignments are stored (for test assertions)."""
        return len(self._by_user)


class In_Memory_OAuthLinkRepository(OAuthLinkRepository):
    """An :class:`OAuthLinkRepository` backed by an in-process dictionary (Req 5.2).

    Satisfies the :class:`~edu_it_domain.ports.oauth_link.OAuthLinkRepository`
    protocol, so the OAuth sign-in use cases (task 16.1) record and resolve the
    durable ``oauth_link`` exactly as they do against the PostgreSQL adapter. A
    link is keyed by its ``(provider, subject)`` pair; :meth:`add` rejects a
    second link for the same pair, so one external identity never maps to two
    local Users and a duplicate account cannot be created (Req 10.4). No internal
    numeric key is held — the owning user is referenced by its ``stable_id`` —
    matching the domain's view.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty OAuth-link repository."""
        self._by_identity: dict[tuple[str, str], OAuthLink] = {}

    def add(self, link: OAuthLink) -> None:
        """Store ``link``; reject a reused ``(provider, subject)`` pair.

        Raises :class:`ValueError` when a link already exists for the identity, so
        one external identity maps to at most one local User (Req 10.4).
        """
        key = (link.provider, link.subject)
        if key in self._by_identity:
            raise ValueError(f"oauth link for identity {key!r} already exists")
        self._by_identity[key] = link

    def get(self, provider: str, subject: str) -> OAuthLink | None:
        """Return the link for ``(provider, subject)``, or ``None`` when absent."""
        return self._by_identity.get((provider, subject))

    def __len__(self) -> int:
        """Return how many OAuth links are stored (for test assertions)."""
        return len(self._by_identity)


class In_Memory_PendingOAuthLinkRepository(PendingOAuthLinkRepository):
    """A :class:`PendingOAuthLinkRepository` backed by an in-process dictionary (Req 5.2).

    Satisfies the
    :class:`~edu_it_domain.ports.pending_oauth_link.PendingOAuthLinkRepository`
    protocol. A token uniquely identifies a pending confirmation (:meth:`add`
    rejects a reused token), and :meth:`mark_consumed` enforces single use
    idempotently — used by both confirmation (on success) and decline
    (immediately) — so a declined token can never later be confirmed (Req 26.4),
    matching the Redis-backed production store the OAuth use cases (task 16.1) run
    against.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty pending-link repository."""
        self._by_token: dict[str, PendingOAuthLink] = {}

    def add(self, pending: PendingOAuthLink) -> None:
        """Store ``pending``; reject a reused ``token``.

        Raises :class:`ValueError` when a record already exists for the token, so a
        token is never reused for two pending links.
        """
        if pending.token in self._by_token:
            raise ValueError(f"pending oauth link for token {pending.token!r} already exists")
        self._by_token[pending.token] = pending

    def get(self, token: str) -> PendingOAuthLink | None:
        """Return the pending link for ``token``, or ``None`` when unrecognized."""
        return self._by_token.get(token)

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Mark the pending link for ``token`` consumed at ``consumed_at``.

        Returns whether an unconsumed record was consumed: ``False`` when the token
        is unknown or the record was already consumed, so consumption is idempotent
        and single use is enforced across both confirm and decline (Req 26.4).
        """
        pending = self._by_token.get(token)
        if pending is None or pending.is_consumed:
            return False
        self._by_token[token] = replace(pending, consumed_at=consumed_at)
        return True
