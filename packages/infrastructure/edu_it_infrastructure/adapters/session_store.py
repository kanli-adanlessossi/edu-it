"""The Redis-backed server-side ``SessionStore`` adapter (task 7.3, Req 5.1, 5.2, ADR 0004).

:class:`Redis_SessionStore` implements the domain
:class:`~edu_it_domain.ports.session_store.SessionStore` port over Redis, the
store ADR 0004 designates for authoritative *ephemeral* session state. The
client holds only an opaque ``server_side_ref``; the owning user and the
session's expiry live server-side in Redis, so ``apps/api`` can re-verify
ownership on each request and each WebSocket attach/reconnect rather than trust a
client-held claim (ADR 0004). It is substitutable for the in-memory fake without
changing domain code (Req 5.2).

Design:

- **One hash per session** — each session is a Redis hash at
  ``{namespace}:session:{server_side_ref}`` holding the owning user's
  ``stable_id``, the expiry instant (ISO-8601, aware UTC), and a ``revoked``
  flag. The reference is the lookup key, so :meth:`resolve` is a single hash
  read.
- **One index set per user** — each user's session references are tracked in a
  Redis set at ``{namespace}:user:{user_stable_id}`` so a completed password
  reset can revoke every session the user owns without scanning the keyspace
  (Req 9.5, 28.1). The set is a best-effort index: :meth:`revoke_all_for_user`
  re-checks activeness per reference through :meth:`revoke`, so a stale entry
  (an expired or already-revoked session) is simply skipped.
- **Authoritative expiry measured by the Clock** — expiry is decided by
  comparing the stored ``expires_at`` against the injected
  :class:`~edu_it_domain.ports.clock.Clock`, so the store honours a controllable
  ``Test_Clock`` in test/dev exactly as the rest of the domain does (Req 5.5). A
  Redis key TTL is *also* set (expiry plus a small grace) purely as a
  self-cleaning backstop; it never decides activeness, so advancing the
  ``Test_Clock`` past expiry makes :meth:`resolve` return ``None`` even though
  the Redis key still physically exists.
- **Revocation is a flag, not a delete** — :meth:`revoke` marks the hash
  ``revoked`` so a revoked reference resolves to ``None`` and re-revoking is an
  idempotent no-op (Req 8.2). The durable ``session`` record in PostgreSQL (the
  identity system of record, ADR 0004) is maintained separately through the
  ``SessionRepository`` port.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import redis
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.session_store import ResolvedSession, SessionStore

__all__ = ["Redis_SessionStore"]

_TTL_GRACE = timedelta(minutes=5)
"""Extra time added to the Redis key TTL beyond the session's logical expiry.

The key TTL is a self-cleaning backstop only; activeness is decided against the
injected Clock. The grace keeps the key readable slightly past logical expiry so
the Clock — not Redis eviction — remains the single authority on expiry.
"""


class Redis_SessionStore(SessionStore):
    """A :class:`SessionStore` backed by Redis hashes (ADR 0004).

    Satisfies the :class:`~edu_it_domain.ports.session_store.SessionStore`
    protocol, so the API creates, resolves, and revokes sessions exactly as it
    does against the in-memory fake.
    """

    def __init__(
        self,
        client: redis.Redis,
        clock: Clock,
        *,
        namespace: str = "edu_it:session",
    ) -> None:
        """Create a session store over ``client`` measuring time with ``clock``.

        ``clock`` is the injected domain :class:`~edu_it_domain.ports.clock.Clock`
        used to decide expiry, so the store honours a ``Test_Clock`` in test/dev.
        ``namespace`` prefixes every Redis key so session keys never collide with
        the task-queue or rate-limit keys sharing the same Redis server
        (ADR 0003/0004).
        """
        self._redis = client
        self._clock = clock
        self._namespace = namespace

    def _key(self, server_side_ref: str) -> str:
        return f"{self._namespace}:{server_side_ref}"

    def _user_index_key(self, user_stable_id: str) -> str:
        return f"{self._namespace}:user:{user_stable_id}"

    @staticmethod
    def _as_str(value: object) -> str:
        """Coerce a Redis reply to ``str`` (client uses ``decode_responses=True``)."""
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def create(
        self,
        server_side_ref: str,
        user_stable_id: str,
        *,
        expires_at: datetime,
    ) -> None:
        """Create ephemeral state for ``server_side_ref`` owned by a user.

        Stores the owning user, the expiry instant, and an un-revoked flag, and
        sets a backstop key TTL of the remaining lifetime plus a small grace. The
        session resolves as active until ``expires_at`` (per the injected Clock)
        or until it is revoked.
        """
        key = self._key(server_side_ref)
        self._redis.hset(
            key,
            mapping={
                "user_stable_id": user_stable_id,
                "expires_at": _to_iso(expires_at),
                "revoked": "0",
            },
        )
        # Track the reference in the owning user's index set so a completed
        # password reset can revoke every session the user owns (Req 9.5, 28.1).
        index_key = self._user_index_key(user_stable_id)
        self._redis.sadd(index_key, server_side_ref)
        ttl_seconds = self._ttl_seconds(expires_at)
        if ttl_seconds is not None:
            self._redis.expire(key, ttl_seconds)
            self._redis.expire(index_key, ttl_seconds)

    def resolve(self, server_side_ref: str) -> ResolvedSession | None:
        """Resolve ``server_side_ref`` to its active session, or ``None``.

        Returns the session only while it is active: an unknown reference, a
        revoked session, or one whose stored expiry is at or before the current
        Clock instant resolves to ``None``, so a stale or invalidated reference
        never authenticates.
        """
        stored = self._redis.hgetall(self._key(server_side_ref))
        if not stored:
            return None
        record = {self._as_str(k): self._as_str(v) for k, v in dict(stored).items()}
        if record.get("revoked") == "1":
            return None
        raw_expires = record.get("expires_at")
        if raw_expires is None:
            return None
        expires_at = _from_iso(raw_expires)
        if self._clock.now() >= expires_at:
            return None
        return ResolvedSession(
            server_side_ref=server_side_ref,
            user_stable_id=record["user_stable_id"],
            expires_at=expires_at,
        )

    def revoke(self, server_side_ref: str) -> bool:
        """Invalidate the session for ``server_side_ref``.

        Returns whether an *active* session was revoked: ``False`` when the
        reference is unknown, already revoked, or already expired (per the
        Clock), so revocation — and therefore sign-out — is idempotent (Req 8.2).
        The hash is flagged rather than deleted so a repeated revoke stays a safe
        no-op until the backstop TTL reclaims the key.
        """
        # resolve() already enforces unknown / revoked / expired -> not active.
        if self.resolve(server_side_ref) is None:
            return False
        self._redis.hset(self._key(server_side_ref), "revoked", "1")
        return True

    def revoke_all_for_user(self, user_stable_id: str) -> int:
        """Invalidate every *active* session owned by ``user_stable_id``.

        Reads the user's index set and re-checks activeness per reference through
        :meth:`revoke`, so only sessions that are currently active (neither
        revoked nor expired per the Clock) are counted and flagged, and a stale
        index entry is simply skipped. A completed password reset thereby leaves
        none of the user's prior sessions resolvable while staying idempotent
        (Req 9.5, 28.1).
        """
        stored = self._redis.smembers(self._user_index_key(user_stable_id))
        revoked_count = 0
        for raw_ref in stored:
            if self.revoke(self._as_str(raw_ref)):
                revoked_count += 1
        return revoked_count

    def _ttl_seconds(self, expires_at: datetime) -> int | None:
        """Backstop key-TTL in whole seconds: remaining lifetime plus grace.

        ``None`` when the session is already expired at creation time (the key is
        still written so an immediate resolve consistently returns ``None`` via
        the Clock check rather than depending on eviction timing).
        """
        remaining = (expires_at + _TTL_GRACE) - self._clock.now()
        seconds = int(remaining.total_seconds())
        return seconds if seconds > 0 else None


def _to_iso(instant: datetime) -> str:
    """Serialize an aware instant to ISO-8601 in UTC for storage."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    """Parse a stored ISO-8601 instant back to an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
