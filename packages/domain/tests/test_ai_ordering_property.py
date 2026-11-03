"""Property 32: The rule-based order is total, deterministic and respects prerequisites.

Feature: ai-content, Property 32

**Validates: Requirements 17.5**
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edu_it_domain.ai.ordering import rule_based_order
from edu_it_domain.config import PATH_RECOMMENDATION_LENGTH
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_MAX_NODES = 40


@st.composite
def _scenario(
    draw: st.DrawFn, *, acyclic: bool
) -> tuple[list[str], dict[str, list[str]], dict[str, int], list[str], list[str]]:
    """Return (all ids, prerequisites, mastery, due, eligible)."""
    size = draw(st.integers(min_value=0, max_value=_MAX_NODES))
    ids = draw(st.permutations([f"concept-{i:02d}" for i in range(size)]))
    prerequisites: dict[str, list[str]] = {}
    for position, item in enumerate(ids):
        candidates = ids[:position] if acyclic else ids
        if candidates:
            chosen = draw(st.lists(st.sampled_from(candidates), max_size=4, unique=True))
            prerequisites[item] = chosen
    mastery = draw(st.dictionaries(st.sampled_from(ids), st.integers(0, 100))) if ids else {}
    due = draw(st.lists(st.sampled_from(ids), unique=True)) if ids else []
    eligible = draw(st.lists(st.sampled_from(ids), unique=True)) if ids else []
    return ids, prerequisites, mastery, due, eligible


def _ancestors(item: str, prerequisites: Mapping[str, Sequence[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(prerequisites.get(item, ()))
    while stack:
        current = stack.pop()
        if current not in seen:
            seen.add(current)
            stack.extend(prerequisites.get(current, ()))
    return seen


@settings(max_examples=200)
@given(data=_scenario(acyclic=True))
def test_acyclic_order_is_deterministic_bounded_and_respects_prerequisites(
    data: tuple[list[str], dict[str, list[str]], dict[str, int], list[str], list[str]],
) -> None:
    _, prerequisites, mastery, due, eligible = data
    order = rule_based_order(eligible, prerequisites, mastery, due)

    # Deterministic: same input, same output, whatever the input collection order.
    assert order == rule_based_order(eligible, prerequisites, mastery, due)
    assert order == rule_based_order(list(reversed(eligible)), prerequisites, mastery, due)

    # Only eligible items, no repeats, bounded length, and total up to the limit.
    assert len(set(order)) == len(order)
    assert set(order) <= set(eligible)
    assert len(order) == min(len(eligible), PATH_RECOMMENDATION_LENGTH)

    # No item precedes an eligible (transitive) prerequisite of it.
    position = {item: index for index, item in enumerate(order)}
    eligible_set = set(eligible)
    for item in order:
        for ancestor in _ancestors(item, prerequisites) & eligible_set:
            assert ancestor in position
            assert position[ancestor] < position[item]


@settings(max_examples=200)
@given(data=_scenario(acyclic=False))
def test_any_graph_including_cycles_gives_a_valid_order_without_error(
    data: tuple[list[str], dict[str, list[str]], dict[str, int], list[str], list[str]],
) -> None:
    _, prerequisites, mastery, due, eligible = data
    order = rule_based_order(eligible, prerequisites, mastery, due)
    assert order == rule_based_order(eligible, prerequisites, mastery, due)
    assert len(set(order)) == len(order)
    assert set(order) <= set(eligible)
    assert len(order) == min(len(eligible), PATH_RECOMMENDATION_LENGTH)


def test_empty_eligible_set_returns_empty_order() -> None:
    assert rule_based_order([], {"a": ["b"]}, {"a": 1}, ["a"]) == ()
