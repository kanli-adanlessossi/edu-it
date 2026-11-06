"""Property 11: Runtime guard decision.

Feature: kubernetes-deployment, Property 11: Runtime guard decision

# Claims: 2.9, 2.10

**Validates: Requirements 2.9, 2.10**
"""

from __future__ import annotations

from edu_it_domain.labs.k8s_rules import runtime_guard
from edu_it_domain.labs.k8s_types import RuntimeDecision
from hypothesis import given, settings
from hypothesis import strategies as st

_environments = st.one_of(
    st.sampled_from(["production", "staging", "development", "dev", "test", ""]),
    st.text(max_size=12),
)


@settings(max_examples=100, deadline=None)
@given(environment=_environments)
def test_runc_refused_in_production_and_warned_elsewhere(environment: str) -> None:
    expected = RuntimeDecision.REFUSE if environment == "production" else RuntimeDecision.WARN
    assert runtime_guard("runc", environment) is expected


@settings(max_examples=100, deadline=None)
@given(runtime_kind=st.sampled_from(["gvisor", "kata"]), environment=_environments)
def test_sandboxed_runtimes_are_ok_in_every_environment(
    runtime_kind: str, environment: str
) -> None:
    assert runtime_guard(runtime_kind, environment) is RuntimeDecision.OK
