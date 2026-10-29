"""The Redis-backed ``IdempotencyStore`` adapter (task 18.5, Req 18.1, 18.2, 23.1-23.3).

:class:`Redis_IdempotencyStore` implements the domain
:class:`~edu_it_domain.ports.idempotency.IdempotencyStore` so the at-most-once
decision is made **at the store**, not by an in-process lock, and therefore holds
across API worker processes:

- :meth:`reserve` claims a key with a single ``SET key value NX`` -- an atomic
  set-if-absent. Exactly one concurrent caller wins (``NEW``); every other caller
  reads the winner's record and gets ``IN_FLIGHT`` (not yet completed),
  ``REPLAY`` (completed, same body) or ``CONFLICT`` (different body fingerprint).
- :meth:`complete` overwrites the caller's own pending record with the result.
- :meth:`release` deletes a pending record (only the owner ever writes after a win)
  so a failed request does not poison its key, and never deletes a completed one.

The result is an application object this infrastructure package must not import,
so the caller injects an ``encode`` / ``decode`` pair (JSON text). Keys expire
after ``ttl`` as a retention bound; a pending record also expires, so a crashed
worker cannot block a key forever.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta

import redis
from edu_it_domain.ports.idempotency import (
    IdempotencyReservation,
    IdempotencyStore,
    ReservationStatus,
)

__all__ = ["Redis_IdempotencyStore"]

#: Retention of a stored result (a replay window), and the bound on a stuck reservation.
DEFAULT_TTL = timedelta(hours=24)
DEFAULT_PENDING_TTL = timedelta(minutes=5)


class Redis_IdempotencyStore(IdempotencyStore):
    """An :class:`IdempotencyStore` whose atomicity comes from Redis ``SET NX``."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        encode: Callable[[object], str],
        decode: Callable[[str], object],
        namespace: str = "edu_it:idempotency",
        ttl: timedelta = DEFAULT_TTL,
        pending_ttl: timedelta = DEFAULT_PENDING_TTL,
    ) -> None:
        """Create a store over ``client``; ``encode``/``decode`` map results to text."""
        self._redis = client
        self._encode = encode
        self._decode = decode
        self._namespace = namespace
        self._ttl = ttl
        self._pending_ttl = pending_ttl

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def reserve(self, key: str, fingerprint: str) -> IdempotencyReservation:
        """Atomically claim ``key``; see the port for the four outcomes."""
        record = json.dumps({"fp": fingerprint, "state": "pending"})
        claimed = self._redis.set(
            self._key(key), record, nx=True, ex=int(self._pending_ttl.total_seconds())
        )
        if claimed:
            return IdempotencyReservation(ReservationStatus.NEW)

        raw = self._redis.get(self._key(key))
        if raw is None:
            # The record expired or was released between SET NX and GET: retry the claim.
            return self.reserve(key, fingerprint)
        stored = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        if stored["fp"] != fingerprint:
            return IdempotencyReservation(ReservationStatus.CONFLICT)
        if stored["state"] == "done":
            return IdempotencyReservation(ReservationStatus.REPLAY, self._decode(stored["result"]))
        return IdempotencyReservation(ReservationStatus.IN_FLIGHT)

    def complete(self, key: str, result: object) -> None:
        """Store ``result`` for the reserved ``key`` so repeats replay it."""
        raw = self._redis.get(self._key(key))
        fingerprint = ""
        if raw is not None:
            fingerprint = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))["fp"]
        record = json.dumps({"fp": fingerprint, "state": "done", "result": self._encode(result)})
        self._redis.set(self._key(key), record, ex=int(self._ttl.total_seconds()))

    def release(self, key: str) -> None:
        """Drop a pending reservation so the key can be retried; keeps completed ones."""
        raw = self._redis.get(self._key(key))
        if raw is None:
            return
        stored = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        if stored["state"] == "pending":
            self._redis.delete(self._key(key))
