"""The PostgreSQL ``ItemFlagReader`` (ai-content task 12.3).

Over ``item_flag`` (revision ``0005_ai_content``). A partial unique index allows
one active flag (``cleared_at`` null) per item. ``flag`` and ``clear_flag`` are the
write operations the Milestone B tests use to set up flagged items; the
Milestone C ``ItemQualityRepository`` (task 19.2) builds on them. Database-agnostic
SQLAlchemy Core, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.learning_repositories import _Base, _require_id
from edu_it_infrastructure.persistence.repositories import _as_aware

if TYPE_CHECKING:
    from collections.abc import Collection

    from edu_it_domain.ports.item_flags import FlagReason

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_ItemFlagRepository"]


class Postgres_ItemFlagRepository(_Base):
    """An :class:`ItemFlagReader` over ``item_flag``."""

    def __init__(self, engine: Any, clock: Any) -> None:
        super().__init__(engine)
        self._clock = clock

    def active_flags(self, keys: Collection[ItemKey]) -> frozenset[ItemKey]:
        by_kind: dict[ItemKind, set[str]] = defaultdict(set)
        for key in keys:
            by_kind[key.kind].add(key.stable_id)
        t = m.item_flag
        found: set[ItemKey] = set()
        with self._engine.connect() as connection:
            for kind, ids in by_kind.items():
                rows = connection.execute(
                    select(t.c.item_stable_id).where(
                        t.c.item_kind == kind.value,
                        t.c.item_stable_id.in_(sorted(ids)),
                        t.c.cleared_at.is_(None),
                    )
                ).all()
                found.update(ItemKey(kind, str(row[0])) for row in rows)
        return frozenset(found)

    def flag(self, key: ItemKey, reason: FlagReason) -> bool:
        """Flag the item. Returns ``False`` when it already has an active flag."""
        t = m.item_flag
        try:
            with self._engine.begin() as connection:
                active = connection.execute(
                    select(t.c.id).where(
                        t.c.item_kind == key.kind.value,
                        t.c.item_stable_id == key.stable_id,
                        t.c.cleared_at.is_(None),
                    )
                ).first()
                if active is not None:
                    return False
                connection.execute(
                    insert(t).values(
                        item_kind=key.kind.value,
                        item_stable_id=key.stable_id,
                        reason_kind=reason.kind.value,
                        detail=reason.detail,
                        flagged_at=_as_aware(self._clock.now()),
                    )
                )
        except IntegrityError:  # a concurrent writer flagged it first
            return False
        return True

    def clear_flag(self, key: ItemKey, task_stable_id: str | None = None) -> None:
        """Clear the active flag, if any, recording the review task that cleared it."""
        t = m.item_flag
        with self._engine.begin() as connection:
            task_id = (
                None
                if task_stable_id is None
                else _require_id(connection, m.review_queue_item, task_stable_id, "task")
            )
            connection.execute(
                update(t)
                .where(
                    t.c.item_kind == key.kind.value,
                    t.c.item_stable_id == key.stable_id,
                    t.c.cleared_at.is_(None),
                )
                .values(cleared_at=_as_aware(self._clock.now()), cleared_by_task=task_id)
            )
