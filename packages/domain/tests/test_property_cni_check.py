"""Property 24: CNI check outcome.

Feature: kubernetes-deployment, Property 24: CNI check outcome

For any probe result, ``cni_check_outcome`` returns ``PASS`` only when the allowed probe is
reachable, the denied probe is blocked and nothing timed out; otherwise it returns ``FAIL``
when the check is required and ``WARN`` when it is not.

**Validates: Requirements 11.9, 11.10, 11.11, 11.12**
"""

from __future__ import annotations

from edu_it_domain.labs.k8s_rules import cni_check_outcome
from edu_it_domain.labs.k8s_types import CniOutcome
from hypothesis import given, settings
from hypothesis import strategies as st


# Claims: 11.9, 11.10, 11.11, 11.12
@settings(max_examples=100, deadline=None)
@given(
    allowed_reachable=st.booleans(),
    denied_blocked=st.booleans(),
    timed_out=st.booleans(),
    required=st.booleans(),
)
def test_cni_check_outcome_passes_only_when_enforcement_is_proven(
    allowed_reachable: bool, denied_blocked: bool, timed_out: bool, required: bool
) -> None:
    outcome = cni_check_outcome(allowed_reachable, denied_blocked, timed_out, required)
    proven = allowed_reachable and denied_blocked and not timed_out
    if proven:
        assert outcome is CniOutcome.PASS
    elif required:
        assert outcome is CniOutcome.FAIL
    else:
        assert outcome is CniOutcome.WARN
