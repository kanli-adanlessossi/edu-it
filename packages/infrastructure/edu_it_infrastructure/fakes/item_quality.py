"""In-memory ``ItemQualityRepository`` and ``ObservationRecorder`` (ai-content task 19.2).

Mirrors the SQL adapter: append-only observations with monotonic ids, statistics
and the cursor replaced together under one lock, an idempotent flag (inherited).
Learner references are kept so tests can assert what was recorded for whom.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from edu_it_domain.ports.item_quality import StoredObservation

from edu_it_infrastructure.fakes.item_flags import In_Memory_ItemFlagRepository

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from edu_it_domain.ai.quality import ItemStat
    from edu_it_domain.ports.item_flags import ItemKey
    from edu_it_domain.ports.item_quality import NewObservation

__all__ = ["In_Memory_ItemQualityRepository"]


class In_Memory_ItemQualityRepository(In_Memory_ItemFlagRepository):
    """In-memory ``ItemQualityRepository`` and ``ObservationRecorder``."""

    def __init__(self) -> None:
        super().__init__()
        self._observations: list[tuple[StoredObservation, NewObservation]] = []
        self._stats: dict[ItemKey, ItemStat] = {}
        self._cursor = 0
        self._last_id = 0
        self._quality_lock = threading.RLock()

    def record(self, obs: NewObservation) -> None:
        with self._quality_lock:
            self._last_id += 1  # monotonic, never reused after a retention delete
            stored = StoredObservation(
                id=self._last_id,
                key=obs.key,
                correct=obs.correct,
                elapsed_ms=obs.elapsed_ms,
            )
            self._observations.append((stored, obs))

    def recorded_for(self, user_id: str) -> tuple[NewObservation, ...]:
        """The observations recorded for ``user_id``, oldest first (for tests)."""
        with self._quality_lock:
            return tuple(raw for _, raw in self._observations if raw.user_id == user_id)

    def cursor(self) -> int:
        with self._quality_lock:
            return self._cursor

    def observations_after(self, last_id: int, limit: int) -> Sequence[StoredObservation]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._quality_lock:
            return [s for s, _ in self._observations if s.id > last_id][:limit]

    def apply(self, stats: Mapping[ItemKey, ItemStat], new_cursor: int) -> None:
        with self._quality_lock:
            self._stats.update(stats)
            self._cursor = new_cursor

    def get(self, key: ItemKey) -> ItemStat | None:
        with self._quality_lock:
            return self._stats.get(key)

    def delete_observations_before(self, cutoff: datetime) -> int:
        with self._quality_lock:
            kept = [pair for pair in self._observations if pair[1].observed_at >= cutoff]
            removed = len(self._observations) - len(kept)
            self._observations = kept
            return removed
