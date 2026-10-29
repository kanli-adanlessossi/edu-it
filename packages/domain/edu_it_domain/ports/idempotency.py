"""The idempotency-store port (Req 18.1, 18.2, 23.1-23.3).

A write endpoint that accepts an ``Idempotency-Key`` must take effect at most
once per key: a repeat with the same key and body returns the stored result, a
reused key with a different body is a conflict, and two concurrent requests with
one key are processed at most once (Req 23.1-23.3,
``.kiro/steering/api-conventions.md`` section 5).

The decision *must* be atomic at the store -- an atomic insert / unique
constraint, or an equivalent row lock -- not an in-process lock, so it holds
across worker processes. That is why the port exposes one atomic
:meth:`IdempotencyStore.reserve` rather than a get-then-put pair.

Framework-free :class:`typing.Protocol` (Req 1.8). The application layer depends
on it; infrastructure supplies the store (and an in-memory fake for tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

__all__ = [
    "IdempotencyReservation",
    "IdempotencyStore",
    "ReservationStatus",
]


class ReservationStatus(Enum):
    """What :meth:`IdempotencyStore.reserve` decided about a key."""

    NEW = "new"
    """First sight of the key: the caller now owns it and must process the request."""

    REPLAY = "replay"
    """Same key and body already completed: return the stored result."""

    CONFLICT = "conflict"
    """The key was used with a different request body (Req 23.3)."""

    IN_FLIGHT = "in_flight"
    """The same request is being processed right now by another caller (Req 23.2)."""


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    """The outcome of reserving an idempotency key."""

    status: ReservationStatus
    """The decision for the key."""

    result: object | None = None
    """The stored result; set only when :attr:`status` is ``REPLAY``."""


@runtime_checkable
class IdempotencyStore(Protocol):
    """Port for the atomic at-most-once guarantee of an ``Idempotency-Key``."""

    def reserve(self, key: str, fingerprint: str) -> IdempotencyReservation:
        """Atomically claim ``key`` for a request whose body hashes to ``fingerprint``.

        Returns ``NEW`` exactly once per key (the caller then processes the
        request and calls :meth:`complete`, or :meth:`release` on failure).
        Afterwards, for the same ``fingerprint``: ``REPLAY`` with the stored
        result once completed, ``IN_FLIGHT`` while still being processed; for a
        different ``fingerprint``: ``CONFLICT``.
        """
        ...

    def complete(self, key: str, result: object) -> None:
        """Store ``result`` for a key the caller reserved, so repeats replay it."""
        ...

    def release(self, key: str) -> None:
        """Drop a reservation whose request failed, so the key may be retried.

        Called when processing raised before any result was produced; a request
        that failed and recorded nothing must not poison its key.
        """
        ...
