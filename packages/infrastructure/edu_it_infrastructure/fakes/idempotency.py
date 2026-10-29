"""In-memory ``IdempotencyStore`` and ``IdGenerator`` fakes (Req 18.2, 23.1-23.3).

:class:`In_Memory_IdempotencyStore` implements the domain
:class:`~edu_it_domain.ports.idempotency.IdempotencyStore` with a dictionary
guarded by a lock, so :meth:`reserve` is atomic within the process. It exists to
exercise the use cases; the production store must make the same decision atomic
across processes (a unique constraint / row lock), which is task 18.5.

:class:`Sequential_IdGenerator` implements
:class:`~edu_it_domain.ports.id_generator.IdGenerator` with a deterministic
counter so tests can assert on minted ``stable_id`` values.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.idempotency import (
    IdempotencyReservation,
    IdempotencyStore,
    ReservationStatus,
)

__all__ = ["In_Memory_IdempotencyStore", "Sequential_IdGenerator"]


@dataclass(slots=True)
class _Entry:
    fingerprint: str
    completed: bool = False
    result: object | None = None


class In_Memory_IdempotencyStore(IdempotencyStore):
    """An :class:`IdempotencyStore` backed by a lock-guarded dictionary."""

    __test__ = False

    def __init__(self) -> None:
        """Create an empty store."""
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def reserve(self, key: str, fingerprint: str) -> IdempotencyReservation:
        """Atomically claim ``key``; see the port for the four outcomes."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = _Entry(fingerprint=fingerprint)
                return IdempotencyReservation(ReservationStatus.NEW)
            if entry.fingerprint != fingerprint:
                return IdempotencyReservation(ReservationStatus.CONFLICT)
            if entry.completed:
                return IdempotencyReservation(ReservationStatus.REPLAY, entry.result)
            return IdempotencyReservation(ReservationStatus.IN_FLIGHT)

    def complete(self, key: str, result: object) -> None:
        """Store ``result`` for the reserved ``key``."""
        with self._lock:
            entry = self._entries[key]
            entry.completed = True
            entry.result = result

    def release(self, key: str) -> None:
        """Drop an uncompleted reservation so the key can be retried."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and not entry.completed:
                del self._entries[key]


class Sequential_IdGenerator(IdGenerator):
    """An :class:`IdGenerator` minting ``"<prefix>-1"``, ``"<prefix>-2"``, ..."""

    __test__ = False

    def __init__(self, prefix: str = "id") -> None:
        """Create a generator whose ids start with ``prefix``."""
        self._prefix = prefix
        self._counter = 0

    def new_id(self) -> str:
        """Return the next id in the sequence."""
        self._counter += 1
        return f"{self._prefix}-{self._counter}"
