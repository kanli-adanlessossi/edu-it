"""Property test for concept coverage findings.

Feature: ai-content, Property 16: Coverage findings name every uncovered concept

Validates: Requirements 7.8
"""

from __future__ import annotations

from edu_it_domain.ai.checks import coverage
from hypothesis import given
from hypothesis import strategies as st

_concept = st.text(alphabet="abcdefghij", min_size=1, max_size=3)


@given(
    concepts=st.lists(_concept, max_size=15),
    covered=st.lists(_concept, max_size=15),
)
def test_coverage_names_every_uncovered_concept(concepts: list[str], covered: list[str]) -> None:
    """**Validates: Requirements 7.8**"""
    result = coverage(concepts, covered)
    unique = set(concepts)
    expected = unique - set(covered)

    assert set(result.uncovered) == expected
    assert len(result.uncovered) == len(expected)  # one finding per concept
    assert not set(result.uncovered) & set(covered)
    if unique:
        assert result.share == (len(unique) - len(expected)) / len(unique)
    else:
        assert result.share == 1.0
