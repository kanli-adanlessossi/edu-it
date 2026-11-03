"""Property test for exact duplicate detection on normalised text.

Feature: ai-content, Property 15: Duplicate detection is exact on normalised text

Validates: Requirements 7.7, 7.12
"""

from __future__ import annotations

from edu_it_domain.ai.checks import is_duplicate, normalise
from hypothesis import given
from hypothesis import strategies as st

_text = st.text(max_size=40)
_ws = st.sampled_from([" ", "  ", "\t", "\n", "\u00a0"])


@given(text=_text, corpus=st.lists(_text, max_size=8))
def test_duplicate_iff_normalised_equal(text: str, corpus: list[str]) -> None:
    """**Validates: Requirements 7.7, 7.12**"""
    expected = any(normalise(c) == normalise(text) for c in corpus)
    assert is_duplicate(text, corpus) is expected


@given(text=_text, corpus=st.lists(_text, max_size=8), pad=_ws)
def test_case_and_whitespace_variants_are_duplicates(
    text: str, corpus: list[str], pad: str
) -> None:
    """**Validates: Requirements 7.7, 7.12**"""
    variant = pad + text.upper() + pad
    if normalise(variant) == normalise(text):
        assert is_duplicate(variant, [*corpus, text])


@given(text=_text, corpus=st.lists(_text, max_size=8))
def test_no_duplicate_without_normalised_match(text: str, corpus: list[str]) -> None:
    """**Validates: Requirements 7.7, 7.12**"""
    others = [c for c in corpus if normalise(c) != normalise(text)]
    assert not is_duplicate(text, others)
