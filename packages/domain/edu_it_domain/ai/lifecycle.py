"""Variant lifecycle (Requirements 13.3, 13.4, 14.1, 19.4).

Pure transition table. D6 states and transitions are kept with one addition
(decision D3): ``Withdrawn -> Approved``, the reviewer reinstatement after a
flag review. ``Flagged`` and ``Rejected`` remain terminal.
"""

from __future__ import annotations

from enum import StrEnum


class VariantState(StrEnum):
    """Lifecycle state of a question variant."""

    GENERATED = "Generated"
    VERIFIED = "Verified"
    FLAGGED = "Flagged"
    PENDING_REVIEW = "PendingReview"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SERVED = "Served"
    WITHDRAWN = "Withdrawn"


S = VariantState

TRANSITIONS: dict[VariantState, frozenset[VariantState]] = {
    S.GENERATED: frozenset({S.VERIFIED, S.FLAGGED}),
    S.VERIFIED: frozenset({S.PENDING_REVIEW}),
    # Self loop: request changes (comment) or reviewer edit (new revision).
    S.PENDING_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.PENDING_REVIEW}),
    S.APPROVED: frozenset({S.SERVED}),
    S.SERVED: frozenset({S.WITHDRAWN}),
    # Reviewer reinstatement after a flag review (amendment D3).
    S.WITHDRAWN: frozenset({S.APPROVED}),
    S.FLAGGED: frozenset(),
    S.REJECTED: frozenset(),
}

SERVABLE_STATES: frozenset[VariantState] = frozenset({S.APPROVED, S.SERVED})


class InvalidTransitionError(ValueError):
    """Raised when a state change is not in ``TRANSITIONS``."""

    def __init__(self, current: VariantState, target: VariantState) -> None:
        super().__init__(f"invalid variant transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: VariantState, target: VariantState) -> bool:
    """Return True when ``current -> target`` is allowed."""
    return target in TRANSITIONS[current]


def transition(current: VariantState, target: VariantState) -> VariantState:
    """Return ``target`` when the move is allowed, else raise."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)
    return target


def is_servable(state: VariantState) -> bool:
    """Only Approved and Served variants may be served (Requirement 14.1)."""
    return state in SERVABLE_STATES
