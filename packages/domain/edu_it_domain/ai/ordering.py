"""Path advisor ordering rules (ai-content Requirements 17.3, 17.4, 17.5, 17.9).

Pure functions, no port, clock or model dependency.

``rule_based_order`` is the deterministic fallback of the Path_Advisor. It is
total (any eligible set, any prerequisite graph, cycles included) and orders the
eligible items so that no item precedes an eligible prerequisite of it, then
due reviews first, then lowest mastery, then ``stable_id``. It returns at most
``PATH_RECOMMENDATION_LENGTH`` items; cutting the list keeps a prefix, so the
prerequisite rule still holds for what is returned.

``check_llm_order`` decides whether an order returned by the model may be
persisted: only when every returned id is an Eligible_Item (an unknown,
unapproved or locked id is never eligible). Otherwise the caller discards the
model output, persists ``rule_based_order`` and records ``reason`` in the audit
log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from edu_it_domain.config import PATH_RECOMMENDATION_LENGTH

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

__all__ = ["OrderCheck", "check_llm_order", "rule_based_order"]


def _eligible_ancestors(
    eligible: frozenset[str],
    prerequisites: Mapping[str, Collection[str]],
) -> dict[str, frozenset[str]]:
    """For each eligible item, the eligible items it transitively requires.

    The walk goes through ineligible items too, so a locked intermediate concept
    does not hide an eligible ancestor behind it.
    """
    result: dict[str, frozenset[str]] = {}
    for item in eligible:
        seen: set[str] = set()
        stack = list(prerequisites.get(item, ()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(prerequisites.get(current, ()))
        result[item] = frozenset((seen & eligible) - {item})
    return result


def rule_based_order(
    eligible: Collection[str],
    prerequisites: Mapping[str, Collection[str]],
    mastery: Mapping[str, int],
    due: Collection[str],
    *,
    limit: int = PATH_RECOMMENDATION_LENGTH,
) -> tuple[str, ...]:
    """Return the rule-based order of ``eligible`` items, at most ``limit`` long.

    ``prerequisites`` maps an item to the items it directly requires; ``mastery``
    maps an item to its integer score (missing means 0); ``due`` holds the items
    with a review due. Items in a prerequisite cycle are ordered by the usual
    key, so the function never fails.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    items = frozenset(eligible)
    due_set = frozenset(due)
    ancestors = _eligible_ancestors(items, prerequisites)

    def key(item: str) -> tuple[int, int, str]:
        return (0 if item in due_set else 1, mastery.get(item, 0), item)

    remaining = set(items)
    ordered: list[str] = []
    while remaining and len(ordered) < limit:
        # Ready: every remaining ancestor is also a descendant (a cycle member).
        # With no cycle this is exactly "no remaining ancestor".
        ready = [
            item
            for item in remaining
            if all(item in ancestors[a] for a in ancestors[item] & remaining)
        ]
        chosen = min(ready, key=key)
        ordered.append(chosen)
        remaining.discard(chosen)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class OrderCheck:
    """The verdict on an order returned by the model."""

    accepted: bool
    ordering: tuple[str, ...]
    """The returned order when accepted, else empty."""

    reason: str | None = None
    """Why the order was discarded; ``None`` when accepted."""


def check_llm_order(
    returned: Sequence[str],
    eligible: Collection[str],
    *,
    limit: int = PATH_RECOMMENDATION_LENGTH,
) -> OrderCheck:
    """Accept ``returned`` only when every id is an Eligible_Item (Req 17.3, 17.4).

    A repeated id or an order longer than ``limit`` is also discarded, because
    the persisted order is a list of distinct items of bounded length.
    """
    allowed = frozenset(eligible)
    rejected = sorted({item for item in returned if item not in allowed})
    if rejected:
        return OrderCheck(False, (), "ids not eligible: " + ", ".join(rejected))
    if len(set(returned)) != len(returned):
        return OrderCheck(False, (), "duplicate ids in order")
    if len(returned) > limit:
        return OrderCheck(False, (), f"order longer than {limit}")
    return OrderCheck(True, tuple(returned))
