"""Property 34: Termination grace arithmetic.

Feature: kubernetes-deployment, Property 34: Termination grace arithmetic

For any drain period in 5 to 300, any preStop delay of 0 or more and any grace period,
``min_termination_grace(drain, prestop)`` equals ``drain + prestop + 10``, and
``check_termination_grace`` accepts the combination if and only if the grace period is at
least that sum. A rejection names the three values.

**Validates: Requirements 14.2, 14.4, 14.12**
"""

from __future__ import annotations

import pytest
from edu_it_domain.termination import (
    TerminationGraceError,
    check_termination_grace,
    min_termination_grace,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_drain = st.integers(min_value=5, max_value=300)
_prestop = st.integers(min_value=0, max_value=600)
_grace = st.integers(min_value=0, max_value=1500)


# Claims: 14.2, 14.4, 14.12
@settings(max_examples=100, deadline=None)
@given(drain=_drain, prestop=_prestop)
def test_min_termination_grace_is_drain_plus_prestop_plus_ten(drain: int, prestop: int) -> None:
    assert min_termination_grace(drain, prestop) == drain + prestop + 10


# Claims: 14.2, 14.4, 14.12
@settings(max_examples=100, deadline=None)
@given(drain=_drain, prestop=_prestop, grace=_grace)
def test_check_accepts_iff_grace_at_least_minimum(drain: int, prestop: int, grace: int) -> None:
    minimum = drain + prestop + 10
    if grace >= minimum:
        check_termination_grace(drain, prestop, grace)
    else:
        with pytest.raises(TerminationGraceError) as excinfo:
            check_termination_grace(drain, prestop, grace)
        message = str(excinfo.value)
        assert str(grace) in message
        assert str(drain) in message
        assert str(prestop) in message
        assert (excinfo.value.drain, excinfo.value.prestop, excinfo.value.grace) == (
            drain,
            prestop,
            grace,
        )


# Claims: 14.2, 14.4, 14.12
@settings(max_examples=100, deadline=None)
@given(drain=_drain, prestop=_prestop, extra=st.integers(min_value=0, max_value=500))
def test_boundary_and_above_accepted_one_below_rejected(
    drain: int, prestop: int, extra: int
) -> None:
    minimum = min_termination_grace(drain, prestop)
    check_termination_grace(drain, prestop, minimum + extra)
    with pytest.raises(TerminationGraceError):
        check_termination_grace(drain, prestop, minimum - 1)
