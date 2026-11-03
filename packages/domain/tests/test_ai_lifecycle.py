"""Tests for the variant lifecycle."""

from __future__ import annotations

import pytest
from edu_it_domain.ai.lifecycle import (
    SERVABLE_STATES,
    TRANSITIONS,
    InvalidTransitionError,
    VariantState,
    can_transition,
    is_servable,
    transition,
)
from hypothesis import given
from hypothesis import strategies as st

S = VariantState
states = st.sampled_from(list(VariantState))

ALLOWED = {
    (S.GENERATED, S.VERIFIED),
    (S.GENERATED, S.FLAGGED),
    (S.VERIFIED, S.PENDING_REVIEW),
    (S.PENDING_REVIEW, S.APPROVED),
    (S.PENDING_REVIEW, S.REJECTED),
    (S.PENDING_REVIEW, S.PENDING_REVIEW),
    (S.APPROVED, S.SERVED),
    (S.SERVED, S.WITHDRAWN),
    (S.WITHDRAWN, S.APPROVED),
}


def test_transition_table_is_exactly_the_amended_d6() -> None:
    """Validates: Requirements 13.3, 13.4"""
    actual = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}
    assert actual == ALLOWED


def test_withdrawn_can_be_reinstated_by_reviewer() -> None:
    """Validates: Requirements 19.4"""
    assert transition(S.WITHDRAWN, S.APPROVED) is S.APPROVED


def test_flagged_and_rejected_are_terminal() -> None:
    """Validates: Requirements 13.3"""
    for state in (S.FLAGGED, S.REJECTED):
        assert TRANSITIONS[state] == frozenset()


def test_only_approved_and_served_are_servable() -> None:
    """Validates: Requirements 14.1"""
    assert {S.APPROVED, S.SERVED} == SERVABLE_STATES
    assert not is_servable(S.PENDING_REVIEW)
    assert not is_servable(S.WITHDRAWN)


def test_invalid_transition_raises() -> None:
    """Validates: Requirements 13.4"""
    with pytest.raises(InvalidTransitionError):
        transition(S.GENERATED, S.APPROVED)


@given(states, states)
def test_transition_agrees_with_table(a: VariantState, b: VariantState) -> None:
    """Validates: Requirements 13.3, 13.4, 14.1"""
    if (a, b) in ALLOWED:
        assert transition(a, b) is b
    else:
        assert not can_transition(a, b)
        with pytest.raises(InvalidTransitionError):
            transition(a, b)
