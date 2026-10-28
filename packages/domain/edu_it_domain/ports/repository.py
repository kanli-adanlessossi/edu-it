"""The identity-persistence repository ports (Req 5.1, ADR 0004).

The durable identity records — the ``user`` and ``session`` rows of the data
model's identity group — are read and written through the repository ports
declared here. Per ADR 0004 the durable ``session`` record in PostgreSQL is the
identity *system of record* (the fast, ephemeral session state lives in Redis
behind the separate :mod:`~edu_it_domain.ports.session_store` port), so these
ports expose exactly the durable-record operations the account system needs
without leaking a concrete database or ORM.

These are framework-free :class:`typing.Protocol` ports in the same spirit as
the five service ports: the application layer depends on the protocol, and the
infrastructure layer supplies a PostgreSQL (SQLAlchemy Core) implementation and,
for tests, an in-memory one. The internal numeric primary key of a row is never
exposed here; callers address a user by its immutable ``stable_id`` and a
session by its opaque ``server_side_ref`` (D3 Section 1, D2 Section 4), so the
domain never sees an internal key.

Scope note: this phase introduces the minimum repository surface the session
store and the account adapters need — creating/looking up a user and
creating/revoking/reading a durable session record. The account *use cases* that
orchestrate these (sign-up, sign-in, reset, ...) land in later tasks; this is the
persistence contract they will build on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "USER_STATUS_UNVERIFIED",
    "USER_STATUS_VERIFIED",
    "SessionRecord",
    "SessionRepository",
    "UserRecord",
    "UserRepository",
]

#: The :attr:`UserRecord.status` stamped on a freshly created, not-yet-verified
#: user (Req 7.1). Opening a valid verification link transitions the user to
#: :data:`USER_STATUS_VERIFIED`.
USER_STATUS_UNVERIFIED: Final[str] = "unverified"

#: The :attr:`UserRecord.status` of a user whose email has been verified (Req
#: 7.4). This is the only status transition the foundations phase performs on a
#: user: unverified → verified, driven by consuming a verification link.
USER_STATUS_VERIFIED: Final[str] = "verified"


@dataclass(frozen=True, slots=True)
class UserRecord:
    """A durable ``user`` row as seen by the domain (never its internal key).

    Carries only the API-safe identity attributes of the ``user`` table; the
    internal numeric primary key (``id``) is deliberately absent so the domain
    addresses a user solely by its immutable, opaque :attr:`stable_id`
    (D3 Section 1, Req 4.4).
    """

    stable_id: str
    """The user's immutable, unique public identifier."""

    email: str
    """The user's email address (the sign-in identifier)."""

    locale: str
    """The user's preferred locale (``fr`` or ``en``)."""

    status: str
    """The account status (e.g. unverified, active), per the identity model."""

    display_name: str | None = None
    """The optional display name."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A durable ``session`` row as seen by the domain (never its internal key).

    The durable record is the identity system of record for a session (ADR 0004):
    it ties an opaque :attr:`server_side_ref` to the owning user and records the
    session's expiry and, once revoked, its revocation instant. The ephemeral
    fast-path state lives in Redis behind the ``SessionStore`` port; this record
    is what persists across restarts and is covered by GDPR export/deletion.
    """

    server_side_ref: str
    """The opaque reference the client holds; the lookup key for the session."""

    user_stable_id: str
    """The ``stable_id`` of the user that owns the session."""

    expires_at: datetime
    """The instant (aware UTC) at which the session expires."""

    revoked_at: datetime | None = None
    """The instant the session was revoked, or ``None`` while it is live."""

    @property
    def is_revoked(self) -> bool:
        """Whether the durable record has been marked revoked."""
        return self.revoked_at is not None


@runtime_checkable
class UserRepository(Protocol):
    """Port for reading and writing durable ``user`` identity records."""

    def add(self, user: UserRecord) -> None:
        """Insert ``user`` as a new durable record.

        Raises :class:`ValueError` when a user with the same ``stable_id`` or
        the same email already exists, so a duplicate account cannot be created.
        """
        ...

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:
        """Return the user with ``stable_id``, or ``None`` when absent."""
        ...

    def get_by_email(self, email: str) -> UserRecord | None:
        """Return the user with ``email``, or ``None`` when absent."""
        ...

    def mark_verified(self, stable_id: str) -> bool:
        """Mark the user with ``stable_id`` as having a verified email.

        Returns whether an unverified user was transitioned to verified:
        ``True`` when the user existed and was unverified, ``False`` when no user
        exists for ``stable_id`` or the user was already verified. Consuming a
        verification link (verification-link consumption use case) is thereby
        idempotent at the store boundary — a second successful link is impossible
        (single use, Req 7.4) and a redundant transition changes nothing (Req 7.4,
        7.5).
        """
        ...


@runtime_checkable
class SessionRepository(Protocol):
    """Port for reading and writing durable ``session`` identity records."""

    def add(self, session: SessionRecord) -> None:
        """Insert ``session`` as a new durable record.

        Raises :class:`ValueError` when a session with the same
        ``server_side_ref`` already exists.
        """
        ...

    def get(self, server_side_ref: str) -> SessionRecord | None:
        """Return the session for ``server_side_ref``, or ``None`` when absent."""
        ...

    def revoke(self, server_side_ref: str, *, revoked_at: datetime) -> bool:
        """Mark the session for ``server_side_ref`` revoked at ``revoked_at``.

        Returns whether a live session was revoked: ``False`` when no session
        exists for the reference or it was already revoked, so revocation is
        idempotent.
        """
        ...

    def revoke_all_for_user(self, user_stable_id: str, *, revoked_at: datetime) -> int:
        """Revoke every live session owned by ``user_stable_id``.

        Returns the number of sessions newly revoked. Used by password reset,
        which revokes all existing sessions (Req 9.5), and account deletion,
        which revokes all sessions (Req 14.3).
        """
        ...
