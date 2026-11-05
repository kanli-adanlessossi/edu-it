"""Property test for ``compute_traceability_gaps`` (tools/check_traceability.py).

Feature: kubernetes-deployment, Property 30: Traceability gaps

For any set of acceptance criterion ids and set of claims, the unclaimed ids are the set
difference, the unknown claimed ids are the reverse difference, and the spec check exits
non-zero if and only if either set is not empty.

**Validates: Requirements 26.1, 26.2**
"""

from __future__ import annotations

import pytest
from check_traceability import _id_key, compute_traceability_gaps
from hypothesis import given, settings
from hypothesis import strategies as st

_ids = st.builds(lambda a, b: f"{a}.{b}", st.integers(1, 30), st.integers(1, 12))
_id_collections = st.lists(_ids, max_size=40)


@settings(max_examples=100)
@given(criteria=_id_collections, claims=_id_collections)
def test_gaps_are_the_two_set_differences(criteria: list[str], claims: list[str]) -> None:
    unclaimed, unknown = compute_traceability_gaps(criteria, claims)

    assert set(unclaimed) == set(criteria) - set(claims)
    assert set(unknown) == set(claims) - set(criteria)
    assert len(unclaimed) == len(set(unclaimed))
    assert len(unknown) == len(set(unknown))
    assert unclaimed == sorted(unclaimed, key=_id_key)
    assert unknown == sorted(unknown, key=_id_key)


@settings(max_examples=100)
@given(criteria=_id_collections, claims=_id_collections)
def test_spec_exit_code_is_nonzero_iff_a_gap_exists(
    criteria: list[str], claims: list[str]
) -> None:
    import check_traceability as module

    unclaimed, unknown = compute_traceability_gaps(criteria, claims)
    original = module.check_spec
    module.check_spec = lambda: (unclaimed, unknown)  # type: ignore[assignment]
    try:
        code = module._main_spec()
    finally:
        module.check_spec = original  # type: ignore[assignment]

    assert (code != 0) == bool(unclaimed or unknown)


@settings(max_examples=50)
@given(criteria=_id_collections)
def test_identical_sets_have_no_gaps(criteria: list[str]) -> None:
    assert compute_traceability_gaps(criteria, criteria) == ([], [])


def test_known_example() -> None:
    assert compute_traceability_gaps(["1.1", "1.2", "2.10"], ["1.2", "9.1", "2.2"]) == (
        ["1.1", "2.10"],
        ["2.2", "9.1"],
    )


@pytest.mark.parametrize("claims", [[], ["1.1"]])
def test_empty_criteria_make_every_claim_unknown(claims: list[str]) -> None:
    assert compute_traceability_gaps([], claims) == ([], sorted(claims))
