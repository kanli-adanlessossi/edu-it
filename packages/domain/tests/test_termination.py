"""Example tests for the termination grace arithmetic (Requirements 14.2, 14.4, 14.12)."""

from __future__ import annotations

import pytest
from edu_it_domain.termination import (
    TerminationGraceError,
    check_termination_grace,
    min_termination_grace,
)


def test_minimum_is_drain_plus_prestop_plus_ten() -> None:
    assert min_termination_grace(30, 5) == 45
    assert min_termination_grace(5, 0) == 15
    assert min_termination_grace(300, 60) == 370


def test_grace_at_or_above_minimum_is_accepted() -> None:
    check_termination_grace(30, 5, 45)
    check_termination_grace(30, 5, 100)


def test_grace_below_minimum_names_the_three_values() -> None:
    with pytest.raises(TerminationGraceError) as exc:
        check_termination_grace(30, 5, 44)
    message = str(exc.value)
    assert "30" in message
    assert "5" in message
    assert "44" in message
    assert "45" in message
