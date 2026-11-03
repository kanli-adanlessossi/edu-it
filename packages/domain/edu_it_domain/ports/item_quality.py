"""The item quality ports (ai-content Req 19.1, 21.4, 21.6, 23.4; task 19.2).

``ObservationRecorder`` is the narrow port the learning use cases write to: every
quiz and review answer appends one observation. ``ItemQualityRepository`` is the
worker's side. It reads observations after a cursor, writes the folded
statistics and the new cursor in one transaction, and adds ``flag`` and
``clear_flag`` to the ``ItemFlagReader`` of task 12.3.

Observations hold a learner reference, so ``item_observation`` is a personal-data
table. Statistics (``ItemStat``) are aggregates with no learner link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from edu_it_domain.ai.quality import ItemObservation
from edu_it_domain.ports.item_flags import ItemFlagReader, ItemKey

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from edu_it_domain.ai.quality import ItemStat
    from edu_it_domain.ports.item_flags import FlagReason

__all__ = [
    "ItemQualityRepository",
    "NewObservation",
    "ObservationRecorder",
    "StoredObservation",
]


@dataclass(frozen=True, slots=True)
class NewObservation:
    """One answer to be recorded. ``elapsed_ms`` is optional and already clamped."""

    user_id: str
    key: ItemKey
    correct: bool
    observed_at: datetime
    elapsed_ms: int | None = None
    variant_ref: str | None = None
    """Set when the answered item was a served variant (``key`` is then the variant)."""


@dataclass(frozen=True, slots=True)
class StoredObservation:
    """An observation read back by the worker. It carries no learner reference."""

    id: int
    key: ItemKey
    correct: bool
    elapsed_ms: int | None = None

    def as_observation(self) -> ItemObservation:
        """The pure-domain form used by :func:`edu_it_domain.ai.quality.fold`."""
        return ItemObservation(
            (self.key.kind.value, self.key.stable_id), self.correct, self.elapsed_ms
        )


@runtime_checkable
class ObservationRecorder(Protocol):
    """Write side used by ``AttemptQuizUseCase`` and ``SubmitReviewUseCase``."""

    def record(self, obs: NewObservation) -> None:
        """Append one observation (append-only)."""
        ...


@runtime_checkable
class ItemQualityRepository(ItemFlagReader, Protocol):
    """Statistics, cursor and flag write side; extends the flag reader."""

    def cursor(self) -> int:
        """The id of the last observation folded into the statistics (0 at the start)."""
        ...

    def observations_after(self, last_id: int, limit: int) -> Sequence[StoredObservation]:
        """Up to ``limit`` observations with ``id > last_id``, ordered by id."""
        ...

    def apply(self, stats: Mapping[ItemKey, ItemStat], new_cursor: int) -> None:
        """Store the statistics and the new cursor in one transaction."""
        ...

    def get(self, key: ItemKey) -> ItemStat | None:
        """The stored statistics of an item, or ``None`` if it has none."""
        ...

    def delete_observations_before(self, cutoff: datetime) -> int:
        """Delete observations with ``observed_at < cutoff``; return the row count.

        Statistics already folded are aggregates with no learner link and are untouched.
        """
        ...

    def flag(self, key: ItemKey, reason: FlagReason) -> bool:
        """Flag the item. ``False`` when it already has an active flag (idempotent)."""
        ...

    def clear_flag(self, key: ItemKey, task_stable_id: str | None = None) -> None:
        """Clear the active flag, if any, recording the review task that cleared it."""
        ...
