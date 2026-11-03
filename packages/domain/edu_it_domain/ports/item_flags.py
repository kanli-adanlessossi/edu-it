"""The ``ItemFlagReader`` port (ai-content Req 14.1, 14.2, 20.3, 23.4).

An item is flagged when its statistics or learner reports say it may be wrong.
A flagged item is never served until a reviewer clears the flag. Serving only
needs the read side: ``active_flags`` answers which of some items carry an
active flag. The write side (``flag``, ``clear_flag``, statistics) belongs to the
Milestone C ``ItemQualityRepository``, which extends this reader (task 19.2).

Items are addressed by ``(kind, stable_id)``; internal keys never cross this
port. The table holds no personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection

__all__ = [
    "FlagReason",
    "FlagReasonKind",
    "ItemFlagReader",
    "ItemFlagWriter",
    "ItemKey",
    "ItemKind",
]


class ItemKind(StrEnum):
    """What kind of content an item is."""

    QUESTION = "question"
    VARIANT = "variant"
    LESSON = "lesson"
    LAB = "lab"


class FlagReasonKind(StrEnum):
    """Why an item was flagged."""

    REPORTS = "reports"
    STATISTICS = "statistics"


@dataclass(frozen=True, slots=True)
class ItemKey:
    """The address of one item."""

    kind: ItemKind
    stable_id: str


@dataclass(frozen=True, slots=True)
class FlagReason:
    """The reason recorded with a flag."""

    kind: FlagReasonKind
    detail: str = ""


@runtime_checkable
class ItemFlagReader(Protocol):
    """Read side of the item flags."""

    def active_flags(self, keys: Collection[ItemKey]) -> frozenset[ItemKey]:
        """The subset of ``keys`` that has an active flag (not cleared). Unknown
        items are simply absent from the result; an empty input gives an empty set."""
        ...


@runtime_checkable
class ItemFlagWriter(ItemFlagReader, Protocol):
    """Write side of the item flags (implemented by the ``ItemQualityRepository``)."""

    def flag(self, key: ItemKey, reason: FlagReason) -> bool:
        """Flag the item. ``False`` when it already has an active flag (idempotent)."""
        ...

    def clear_flag(self, key: ItemKey, task_stable_id: str | None = None) -> None:
        """Clear the active flag, if any, recording the review task that cleared it."""
        ...
