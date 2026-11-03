"""Property 17: Readability and similarity findings follow the limits.

Validates: Requirements 7.9, 7.10
"""

from __future__ import annotations

from edu_it_domain.ai.checks import (
    SHINGLE_SIZE,
    readability_en,
    readability_fr,
    similarity,
)
from edu_it_domain.config import READABILITY_BOUNDS, SIMILARITY_LIMIT
from hypothesis import given, settings
from hypothesis import strategies as st

_word = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8)
_text = st.lists(_word, min_size=0, max_size=40).map(" ".join)
_long_text = st.lists(_word, min_size=SHINGLE_SIZE, max_size=30).map(" ".join)


def _readability_finding(score: float, bounds: tuple[float, float]) -> bool:
    return score < bounds[0] or score > bounds[1]


def _similarity_finding(score: float, limit: float) -> bool:
    return score > limit


@settings(max_examples=100, deadline=None)
@given(text=_text)
def test_readability_finding_iff_outside_bounds(text: str) -> None:
    """**Validates: Requirements 7.9**"""
    low, high = READABILITY_BOUNDS
    for score in (readability_en(text), readability_fr(text)):
        assert _readability_finding(score, READABILITY_BOUNDS) == (not low <= score <= high)


@settings(max_examples=100, deadline=None)
@given(
    score=st.floats(min_value=-200, max_value=300, allow_nan=False),
    a=st.floats(min_value=-100, max_value=200, allow_nan=False),
    b=st.floats(min_value=-100, max_value=200, allow_nan=False),
)
def test_readability_finding_for_any_score_and_bounds(score: float, a: float, b: float) -> None:
    """**Validates: Requirements 7.9**"""
    low, high = min(a, b), max(a, b)
    assert _readability_finding(score, (low, high)) == (not low <= score <= high)


@settings(max_examples=100, deadline=None)
@given(text=_text, corpus=st.lists(_text, max_size=4))
def test_similarity_finding_iff_above_limit(text: str, corpus: list[str]) -> None:
    """**Validates: Requirements 7.10**"""
    score = similarity(text, corpus)
    assert 0.0 <= score <= 1.0
    assert _similarity_finding(score, SIMILARITY_LIMIT) == (score > SIMILARITY_LIMIT)


@settings(max_examples=100, deadline=None)
@given(text=_long_text, others=st.lists(_text, max_size=3))
def test_contained_text_scores_one(text: str, others: list[str]) -> None:
    """**Validates: Requirements 7.10**"""
    score = similarity(text, [*others, text])
    assert score == 1.0
    assert _similarity_finding(score, SIMILARITY_LIMIT)


@settings(max_examples=100, deadline=None)
@given(text=_text, corpus=st.lists(_text, max_size=3))
def test_disjoint_vocabulary_scores_zero(text: str, corpus: list[str]) -> None:
    """**Validates: Requirements 7.10**"""
    # Corpus tokens are letters only; the text's tokens all contain a digit.
    own = " ".join(f"{w}9" for w in text.split()) + " " + " ".join(["x9"] * SHINGLE_SIZE)
    score = similarity(own, corpus)
    assert score == 0.0
    assert not _similarity_finding(score, SIMILARITY_LIMIT)
