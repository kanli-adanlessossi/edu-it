"""Property 14 for ProvisionSandbox (kubernetes-deployment task 8.5).

Feature: kubernetes-deployment, Property 14: Capacity failures do not consume the retry allowance

For any sequence of provisioning outcomes, the retry allowance used equals the number of
non-capacity failures in the sequence, and ``edu_it_lab_quota_rejections_total`` equals the
number of capacity failures.

**Validates: Requirements 22.4, 22.5**
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from edu_it_application.labs import ProvisionOutcome, ProvisionSandbox
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.sandbox_provider import ProvisionMode
from edu_it_infrastructure.telemetry import PrometheusLabMetrics
from hypothesis import given, settings
from hypothesis import strategies as st
from prometheus_client import CollectorRegistry

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)
_STEP = max(config.LAB_PROVISION_CAPACITY_BACKOFF, config.LAB_PROVISION_MIN_INTERVAL)
# A generous timeout so that long capacity runs never reach the capacity-failure deadline;
# that deadline is covered by the example tests in test_provision_sandbox_capacity.py.
_CONFIG = SimpleNamespace(
    LAB_PROVISION_MAX_RETRIES=config.LAB_PROVISION_MAX_RETRIES,
    LAB_PROVISION_MIN_INTERVAL=config.LAB_PROVISION_MIN_INTERVAL,
    LAB_PROVISION_CAPACITY_BACKOFF=config.LAB_PROVISION_CAPACITY_BACKOFF,
    LAB_PROVISION_TIMEOUT=timedelta(days=30),
)
_CAPACITY = (ProvisionMode.CAPACITY, ProvisionMode.CAPACITY_PARTIAL)
# Non-terminal outcomes: capacity rejections (either kind) and ordinary failures, with at
# most MAX_RETRIES ordinary failures so the allowance is never exhausted mid-sequence.
_outcomes = (
    st.lists(
        st.sampled_from([*_CAPACITY, ProvisionMode.FAIL]),
        max_size=25,
    )
    .filter(lambda ms: ms.count(ProvisionMode.FAIL) <= config.LAB_PROVISION_MAX_RETRIES)
)


@settings(max_examples=150, deadline=None)
@given(modes=_outcomes, then_succeed=st.booleans())
def test_capacity_failures_do_not_consume_the_retry_allowance(
    modes: list[ProvisionMode], then_succeed: bool
) -> None:
    # Feature: kubernetes-deployment, Property 14: Capacity failures do not consume the retry
    # allowance
    clock = Test_Clock()
    sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
    sandbox = Fake_SandboxProvider(clock)
    registry = CollectorRegistry()
    job = ProvisionSandbox(
        sessions=sessions,
        catalog=Fake_LabCatalog([_LAB]),
        sandbox=sandbox,
        clock=clock,
        config=_CONFIG,
        metrics=PrometheusLabMetrics(registry),
    )
    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id="s1",
            user_id="u1",
            lab_id="lab1",
            status=SessionStatus.PROVISIONING,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
        )
    )
    script = [*modes, ProvisionMode.OK] if then_succeed else list(modes)
    sandbox.script_provision(*script)

    for _ in script:
        outcome = job.execute("s1")
        assert outcome in (ProvisionOutcome.RETRY_SCHEDULED, ProvisionOutcome.READY)
        clock.advance(_STEP)

    session = sessions.get("s1")
    assert session is not None
    failures = modes.count(ProvisionMode.FAIL)
    capacity = len(modes) - failures
    # The allowance used counts ordinary failures only (plus the final claim on success).
    assert session.provision_attempts == failures + (1 if then_succeed else 0)
    assert session.capacity_attempts == capacity
    assert (registry.get_sample_value("edu_it_lab_quota_rejections_total") or 0.0) == capacity
    expected = SessionStatus.READY if then_succeed else SessionStatus.PROVISIONING
    assert session.status is expected
