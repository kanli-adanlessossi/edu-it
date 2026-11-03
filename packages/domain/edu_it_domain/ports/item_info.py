"""The ``ItemInfoSource`` port (ai-content Req 18.4, 18.7, 19.3; task 21.1).

The quality loop flags an item by ``(kind, stable_id)``. To count the reports of the
item's *current* ``content_version`` and to route the Review_Task to the right
reviewers it needs two facts about the item: its track and its current version. The
catalog and the variant store hold them, so the worker supplies this port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from edu_it_domain.ports.item_flags import ItemKey

__all__ = ["ItemInfo", "ItemInfoSource"]


@dataclass(frozen=True, slots=True)
class ItemInfo:
    """What the quality loop needs to know about one item."""

    track_stable_id: str
    content_version: str | None
    """The version learners currently see; ``None`` when the item has none."""


@runtime_checkable
class ItemInfoSource(Protocol):
    """Port describing an item for the quality loop."""

    def describe(self, key: ItemKey) -> ItemInfo | None:
        """The item's track and current version, or ``None`` when the item is unknown."""
        ...
