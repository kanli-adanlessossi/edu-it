"""In-memory ``ItemFlagReader`` (ai-content task 12.3).

Mirrors the SQL adapter: one active flag per item, cleared flags kept out of
``active_flags``. ``flag`` and ``clear_flag`` let tests set up flagged items.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

    from edu_it_domain.ports.item_flags import FlagReason, ItemKey

__all__ = ["In_Memory_ItemFlagRepository"]


class In_Memory_ItemFlagRepository:
    """In-memory ``ItemFlagReader``."""

    def __init__(self) -> None:
        self._active: dict[ItemKey, FlagReason] = {}
        self._lock = threading.RLock()

    def active_flags(self, keys: Collection[ItemKey]) -> frozenset[ItemKey]:
        with self._lock:
            return frozenset(key for key in keys if key in self._active)

    def flag(self, key: ItemKey, reason: FlagReason) -> bool:
        """Flag the item. Returns ``False`` when it already has an active flag."""
        with self._lock:
            if key in self._active:
                return False
            self._active[key] = reason
            return True

    def clear_flag(self, key: ItemKey, task_stable_id: str | None = None) -> None:
        """Clear the active flag, if any."""
        del task_stable_id  # only the SQL adapter records the clearing task
        with self._lock:
            self._active.pop(key, None)
