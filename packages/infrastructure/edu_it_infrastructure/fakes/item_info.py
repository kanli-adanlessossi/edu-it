"""In-memory ``ItemInfoSource`` (ai-content task 21.1): items are registered by tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edu_it_domain.ports.item_flags import ItemKey
    from edu_it_domain.ports.item_info import ItemInfo

__all__ = ["In_Memory_ItemInfoSource"]


class In_Memory_ItemInfoSource:
    """In-memory ``ItemInfoSource``."""

    def __init__(self) -> None:
        self._items: dict[ItemKey, ItemInfo] = {}

    def set(self, key: ItemKey, info: ItemInfo) -> None:
        """Register or replace an item (for tests)."""
        self._items[key] = info

    def describe(self, key: ItemKey) -> ItemInfo | None:
        return self._items.get(key)
