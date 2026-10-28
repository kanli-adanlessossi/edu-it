"""The server-side ``SessionStore`` port (Req 5.1, ADR 0004).

ADR 0004 pins server-side session backing: the client holds only an opaque
session reference, and the authoritative *ephemeral* session state lives in
Redis, which ``apps/api`` resolves on each request and each WebSocket
attach/reconnect so ownership can be re-verified at the server rather than
inferred from client-held claims. This port is that fast-path store; the durable
``session`` record (the identity system of record) is reached through the
separate :mod:`~edu_it_domain.ports.repository` ``SessionRepository`` port.

The surface is the four operations ADR 0004 calls out: **create** a session for
a user with an expiry, **resolve** it by its opaque reference, **revoke**
(invalidate) it, and let it **expire** — plus a bulk **revoke every session a
user owns**, which a completed password reset needs so the only Session able to
authenticate afterward is the newly established one (Req 9.5, 28.1). Resolution
returns the owning user's ``stable_id`` so ownership is re-verifiable
server-side. A session that is expired (as measured by the injected ``Clock``)
or revoked resolves to ``None``, so a stale or invalidated reference never
authenticates.

This is a framework-free :class:`typing.Protocol` port; the infrastructure layer
supplies a Redis implementation and, for tests, an in-memory one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["ResolvedSession", "SessionStore"]


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """The ephemeral state a resolved, still-active session carries.

    Returned by :meth:`SessionStore.resolve` for a reference that is neither
    expired nor revoked. It names the owning user so the caller can re-verify
    ownership server-side (ADR 0004) without trusting any client-held claim.
    """

    server_side_ref: str
    """The opaque reference that was resolved."""

    user_stable_id: str
    """The ``stable_id`` of the user that owns the session."""

    expires_at: datetime
    """The instant (aware UTC) at which the session expires."""


@runtime_checkable
class SessionStore(Protocol):
    """Port for creating, resolving, and revoking server-side sessions (ADR 0004)."""

    def create(
        self,
        server_side_ref: str,
        user_stable_id: str,
        *,
        expires_at: datetime,
    ) -> None:
        """Create ephemeral state for ``server_side_ref`` owned by a user.

        The session resolves as active until ``expires_at`` (measured by the
        injected ``Clock``) or until it is revoked. The reference is opaque and
        is the only thing the client holds (ADR 0004).
        """
        ...

    def resolve(self, server_side_ref: str) -> ResolvedSession | None:
        """Resolve ``server_side_ref`` to its active session, or ``None``.

        Returns the :class:`ResolvedSession` only while the session is active:
        a reference that is unknown, expired (per the injected ``Clock``), or
        revoked resolves to ``None``, so a stale or invalidated reference never
        authenticates a request.
        """
        ...

    def revoke(self, server_side_ref: str) -> bool:
        """Invalidate the session for ``server_side_ref``.

        Returns whether an active session was revoked: ``False`` when the
        reference is unknown, already revoked, or already expired, so revocation
        (and therefore sign-out) is idempotent (Req 8.2).
        """
        ...

    def revoke_all_for_user(self, user_stable_id: str) -> int:
        """Invalidate every active session owned by ``user_stable_id``.

        Returns the number of active sessions newly revoked. Used by a completed
        password reset, which revokes all of a user's existing ephemeral sessions
        (alongside the durable
        :meth:`~edu_it_domain.ports.repository.SessionRepository.revoke_all_for_user`)
        so that a Session revoked by the reset resolves to ``None`` and is never
        refreshed or reissued, leaving only the newly established Session able to
        authenticate (Req 9.5, 28.1). An already-revoked or already-expired
        session is not counted, so the operation is idempotent.
        """
        ...
