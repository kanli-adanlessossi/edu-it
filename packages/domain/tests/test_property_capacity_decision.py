"""Property 13: capacity requeue decision.

Feature: kubernetes-deployment, Property 13: Capacity requeue decision

For any elapsed time, provisioning timeout and backoff, ``capacity_decision`` returns
``Requeue(backoff)`` when elapsed is below the timeout and ``Fail`` with reason
``capacity`` otherwise.

**Validates: Requirements 22.6, 22.7**
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_domain.labs.k8s_rules import capacity_decision
from edu_it_domain.labs.k8s_types import Fail, Requeue
from hypothesis import given, settings
from hypothesis import strategies as st

_durations = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=1))
_backoffs = st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(hours=1))


# Claims: 22.6, 22.7
@settings(max_examples=100, deadline=None)
@given(elapsed=_durations, provision_timeout=_durations, backoff=_backoffs)
def test_capacity_decision_requeues_below_timeout_and_fails_otherwise(
    elapsed: timedelta, provision_timeout: timedelta, backoff: timedelta
) -> None:
    decision = capacity_decision(elapsed, provision_timeout, backoff)
    if elapsed < provision_timeout:
        assert decision == Requeue(delay=backoff)
    else:
        assert decision == Fail(reason="capacity")


# Claims: 22.6, 22.7
@settings(max_examples=100, deadline=None)
@given(provision_timeout=_durations, backoff=_backoffs)
def test_capacity_decision_boundary_at_timeout_fails(
    provision_timeout: timedelta, backoff: timedelta
) -> None:
    assert capacity_decision(provision_timeout, provision_timeout, backoff) == Fail(
        reason="capacity"
    )
