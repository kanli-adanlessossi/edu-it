"""In-memory server-side ``SessionStore`` fake (Req 5.2, ADR 0004).

:class:`In_Memory_SessionStore` implements the domain
:class:`~edu_it_domain.ports.session_store.SessionStore` port with a plain
in-process dictionary, so the sign-in / sign-out / session-authentication use
cases (task 13.1) and their property tests can be exercised without a live Redis.
It is substitutable for the Redis-backed
:class:`~edu_it_infrastructure.adapters.session_store.Redis_SessionStore` without
changing domain code (Req 5.2): it satisfies the same protocol and enforces the
same ADR 0004 semantics.

The fake mirrors the adapter's two load-bearing behaviors exactly:

- **Authoritative expiry measured by the Clock.** Activeness is decided by
  comparing the stored expiry against the injected
  :class:`~edu_it_domain.ports.clock.Clock`, so advancing a ``Test_Clock`` past
  a session's expiry makes :meth:`resolve` return ``None`` even though the entry
  still physically exists (Req 5.5) — matching the adapter, whose Redis key TTL
  is only a backstop.
- **Revocation is a flag, not a delete.** :meth:`revoke` marks the entry revoked
  so a revoked reference resolves to ``None`` and re-revoking is an idempotent
  no-op (Req 8.2), exactly as the adapter flags its Redis hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.session_store import ResolvedSession, SessionStore

__all__ = ["In_Memory_SessionStore"]


@dataclass(slots=True)
class _Entry:
    """One stored session's ephemeral state (owner, expiry, revoked flag)."""

    user_stable_id: str
    expires_at: datetime
    revoked: bool = False


class In_Memory_SessionStore(SessionStore):
    """A :class:`SessionStore` backed by an in-process dictionary (ADR 0004).

    Satisfies the :class:`~edu_it_domain.ports.session_store.SessionStore`
    protocol, so the application layer creates, resolves, and revokes sessions
    exactly as it does against :class:`Redis_SessionStore`. Expiry is decided
    against the injected ``Clock`` and revocation is a flag, so the fake honours
    a controllable ``Test_Clock`` and keeps revocation idempotent just like the
    adapter.
    """

    __test__ = False

    def __init__(self, clock: Clock) -> None:
        """Create an empty store measuring expiry with ``clock``.

        ``clock`` is the injected domain :class:`~edu_it_domain.ports.clock.Clock`
        used to decide activeness, so the fake honours a ``Test_Clock`` in tests
        exactly as the Redis adapter does.
        """
        self._clock = clock
        self._by_ref: dict[str, _Entry] = {}

    def create(
        self,
        server_side_ref: str,
        user_stable_id: str,
        *,
        expires_at: datetime,
    ) -> None:
        """Create ephemeral state for ``server_side_ref`` owned by a user.

        The session resolves as active until ``expires_at`` (per the injected
        Clock) or until it is revoked.
        """
        self._by_ref[server_side_ref] = _Entry(
            user_stable_id=user_stable_id,
            expires_at=_as_utc(expires_at),
        )

    def resolve(self, server_side_ref: str) -> ResolvedSession | None:
        """Resolve ``server_side_ref`` to its active session, or ``None``.

        Returns the session only while active: an unknown reference, a revoked
        session, or one whose expiry is at or before the current Clock instant
        resolves to ``None``, so a stale or invalidated reference never
        authenticates (Req 8.5).
        """
        entry = self._by_ref.get(server_side_ref)
        if entry is None or entry.revoked:
            return None
        if self._clock.now() >= entry.expires_at:
            return None
        return ResolvedSession(
            server_side_ref=server_side_ref,
            user_stable_id=entry.user_stable_id,
            expires_at=entry.expires_at,
        )

    def revoke(self, server_side_ref: str) -> bool:
        """Invalidate the session for ``server_side_ref``.

        Returns whether an *active* session was revoked: ``False`` when the
        reference is unknown, already revoked, or already expired (per the Clock),
        so revocation — and therefore sign-out — is idempotent (Req 8.2).
        """
        if self.resolve(server_side_ref) is None:
            return False
        self._by_ref[server_side_ref].revoked = True
        return True

    def revoke_all_for_user(self, user_stable_id: str) -> int:
        """Invalidate every *active* session owned by ``user_stable_id``.

        Returns the number of active sessions newly revoked. Only sessions that
        are currently active (neither revoked nor expired per the Clock) are
        counted and flagged, matching :meth:`revoke`, so a completed password
        reset leaves none of a user's prior ephemeral sessions resolvable while
        staying idempotent (Req 9.5, 28.1).
        """
        revoked_count = 0
        for ref in list(self._by_ref):
            if self.resolve(ref) is None:
                continue
            if self._by_ref[ref].user_stable_id == user_stable_id:
                self._by_ref[ref].revoked = True
                revoked_count += 1
        return revoked_count


def _as_utc(instant: datetime) -> datetime:
    """Normalise an instant to aware UTC, matching the Redis adapter's storage."""
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)
