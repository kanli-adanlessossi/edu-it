"""Unit tests for the path advisor ordering rules (ai-content task 22.1).

**Validates: Requirements 17.3, 17.4**
"""

from __future__ import annotations

import pytest
from edu_it_domain.ai.ordering import check_llm_order, rule_based_order
from edu_it_domain.config import PATH_RECOMMENDATION_LENGTH


def test_empty_eligible_set_gives_empty_order() -> None:
    assert rule_based_order([], {}, {}, []) == ()


def test_prerequisite_comes_first_even_through_a_locked_intermediate() -> None:
    # c requires x (not eligible) which requires a: a must still precede c.
    order = rule_based_order(["c", "a"], {"c": ["x"], "x": ["a"]}, {"a": 90, "c": 0}, [])
    assert order == ("a", "c")


def test_due_then_lowest_mastery_then_id() -> None:
    order = rule_based_order(
        ["a", "b", "c", "d"],
        {},
        {"a": 50, "b": 10, "c": 10, "d": 90},
        ["d"],
    )
    assert order == ("d", "b", "c", "a")


def test_order_is_cut_to_the_limit_and_keeps_a_prefix() -> None:
    items = [f"c{i}" for i in range(PATH_RECOMMENDATION_LENGTH + 3)]
    order = rule_based_order(items, {}, {}, [])
    assert len(order) == PATH_RECOMMENDATION_LENGTH
    assert order == tuple(items[:PATH_RECOMMENDATION_LENGTH])


def test_cycle_does_not_fail_and_returns_every_item() -> None:
    order = rule_based_order(["a", "b", "c"], {"a": ["b"], "b": ["a"], "c": ["a"]}, {}, [])
    assert sorted(order) == ["a", "b", "c"]
    assert order[-1] == "c"


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        rule_based_order(["a"], {}, {}, [], limit=-1)


def test_llm_order_of_eligible_ids_is_accepted() -> None:
    check = check_llm_order(["b", "a"], ["a", "b", "c"])
    assert check.accepted
    assert check.ordering == ("b", "a")
    assert check.reason is None


@pytest.mark.parametrize(
    ("returned", "fragment"),
    [
        (["a", "ghost"], "ghost"),
        (["a", "a"], "duplicate"),
        ([f"c{i}" for i in range(PATH_RECOMMENDATION_LENGTH + 1)], "longer"),
    ],
)
def test_llm_order_with_unknown_duplicate_or_too_long_is_discarded(
    returned: list[str], fragment: str
) -> None:
    eligible = {"a", "b", *returned}
    if fragment == "ghost":
        eligible.discard("ghost")
    check = check_llm_order(returned, eligible)
    assert not check.accepted
    assert check.ordering == ()
    assert check.reason is not None
    assert fragment in check.reason
