"""The PostgreSQL ``ItemQualityRepository`` and ``ObservationRecorder`` (ai-content task 19.2).

Over ``item_observation``, ``item_statistic``, ``ai_cursor`` (revision
``0005_ai_content``) and, through :class:`Postgres_ItemFlagRepository`, ``item_flag``.
``apply`` writes the statistics and the cursor in one transaction, so the cursor
never drifts from the data it describes. ``flag`` is idempotent (inherited).
Database-agnostic SQLAlchemy Core, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from edu_it_domain.ai.quality import ItemStat
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import StoredObservation
from sqlalchemy import delete, insert, select, update

from edu_it_infrastructure.persistence.item_flags import Postgres_ItemFlagRepository
from edu_it_infrastructure.persistence.learning_repositories import _require_id
from edu_it_infrastructure.persistence.repositories import _as_aware

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from edu_it_domain.ports.item_quality import NewObservation

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["CURSOR_NAME", "Postgres_ItemQualityRepository"]

#: The ``ai_cursor`` row holding the last ``item_observation`` id folded into the statistics.
CURSOR_NAME = "item_quality"


class Postgres_ItemQualityRepository(Postgres_ItemFlagRepository):
    """An :class:`ItemQualityRepository` and :class:`ObservationRecorder` over SQL."""

    def record(self, obs: NewObservation) -> None:
        with self._engine.begin() as connection:
            user_id = _require_id(connection, m.user, obs.user_id, "user")
            connection.execute(
                insert(m.item_observation).values(
                    user_id=user_id,
                    item_kind=obs.key.kind.value,
                    item_stable_id=obs.key.stable_id,
                    variant_ref=obs.variant_ref,
                    correct=obs.correct,
                    elapsed_ms=obs.elapsed_ms,
                    observed_at=_as_aware(obs.observed_at),
                )
            )

    def cursor(self) -> int:
        with self._engine.connect() as connection:
            return _read_cursor(connection)

    def observations_after(self, last_id: int, limit: int) -> Sequence[StoredObservation]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        t = m.item_observation
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(t.c.id, t.c.item_kind, t.c.item_stable_id, t.c.correct, t.c.elapsed_ms)
                .where(t.c.id > last_id)
                .order_by(t.c.id)
                .limit(limit)
            ).all()
        return [
            StoredObservation(
                id=int(row[0]),
                key=ItemKey(ItemKind(row[1]), str(row[2])),
                correct=bool(row[3]),
                elapsed_ms=None if row[4] is None else int(row[4]),
            )
            for row in rows
        ]

    def apply(self, stats: Mapping[ItemKey, ItemStat], new_cursor: int) -> None:
        t = m.item_statistic
        now = _as_aware(self._clock.now())
        with self._engine.begin() as connection:
            for key in sorted(stats, key=lambda k: (k.kind.value, k.stable_id)):
                stat = stats[key]
                values = {
                    "sample_count": stat.samples,
                    "correct_count": stat.correct_count,
                    "timed_count": stat.time_samples,
                    "total_time_ms": stat.time_total_ms,
                    "correct_rate": stat.correct_rate,
                    "avg_time_ms": stat.avg_time_ms,
                    "updated_at": now,
                }
                where = (t.c.item_kind == key.kind.value, t.c.item_stable_id == key.stable_id)
                updated = connection.execute(update(t).where(*where).values(**values))
                if updated.rowcount == 0:
                    connection.execute(
                        insert(t).values(
                            item_kind=key.kind.value, item_stable_id=key.stable_id, **values
                        )
                    )
            _write_cursor(connection, new_cursor)

    def get(self, key: ItemKey) -> ItemStat | None:
        t = m.item_statistic
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    t.c.sample_count, t.c.correct_count, t.c.timed_count, t.c.total_time_ms
                ).where(t.c.item_kind == key.kind.value, t.c.item_stable_id == key.stable_id)
            ).first()
        if row is None:
            return None
        return ItemStat(int(row[0]), int(row[1]), int(row[2]), int(row[3]))

    def delete_observations_before(self, cutoff: datetime) -> int:
        t = m.item_observation
        with self._engine.begin() as connection:
            result = connection.execute(delete(t).where(t.c.observed_at < _as_aware(cutoff)))
            return int(result.rowcount)


def _read_cursor(connection: Any) -> int:
    value = connection.execute(
        select(m.ai_cursor.c.value).where(m.ai_cursor.c.name == CURSOR_NAME)
    ).scalar_one_or_none()
    return 0 if value is None else int(value)


def _write_cursor(connection: Any, value: int) -> None:
    t = m.ai_cursor
    updated = connection.execute(update(t).where(t.c.name == CURSOR_NAME).values(value=value))
    if updated.rowcount == 0:
        connection.execute(insert(t).values(name=CURSOR_NAME, value=value))
