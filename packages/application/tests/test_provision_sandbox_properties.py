"""Property 8 for ProvisionSandbox (task 6.9).

Feature: hands-on-labs, Property 8: Provisioning is bounded, spaced, and creates at most one sandbox

**Validates: Requirements 6.2, 6.6, 7.6, 25.1, 25.2, 25.3, 25.7**
"""

from __future__ import annotations

from datetime import datetime, timedelta

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
from hypothesis import given, settings
from hypothesis import strategies as st

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)
_MAX_ATTEMPTS = config.LAB_PROVISION_MAX_RETRIES + 1
_MIN_INTERVAL = config.LAB_PROVISION_MIN_INTERVAL

_modes = st.lists(st.sampled_from(list(ProvisionMode)), min_size=0, max_size=6)
# Each delivery is preceded by a clock advance (0 = duplicate/early delivery).
_advances = st.lists(st.integers(min_value=0, max_value=25), min_size=1, max_size=12)


@settings(max_examples=150, deadline=None)
@given(modes=_modes, advances=_advances)
def test_provisioning_is_bounded_spaced_and_creates_at_most_one_sandbox(
    modes: list[ProvisionMode], advances: list[int]
) -> None:
    # Feature: hands-on-labs, Property 8: Provisioning is bounded, spaced, and creates at most one sandbox
    clock = Test_Clock()
    sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
    sandbox = Fake_SandboxProvider(clock)
    sandbox.script_provision(*modes)

    starts: list[datetime] = []
    real_provision = sandbox.provision

    def recording_provision(spec):  # noqa: ANN001, ANN202
        starts.append(clock.now())
        return real_provision(spec)

    sandbox.provision = recording_provision  # type: ignore[method-assign]

    job = ProvisionSandbox(
        sessions=sessions,
        catalog=Fake_LabCatalog([_LAB]),
        sandbox=sandbox,
        clock=clock,
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

    for advance in advances:
        clock.advance(timedelta(seconds=advance))
        outcome = job.execute("s1")

        session = sessions.get("s1")
        assert session is not None
        live = sandbox.list_sandboxes()

        # At most one sandbox exists at any time (25.7, 6.6).
        assert len(live) <= 1
        # Attempts are bounded by 1 + LAB_PROVISION_MAX_RETRIES, and never exceed provision calls.
        assert session.provision_attempts <= _MAX_ATTEMPTS
        assert len(starts) <= _MAX_ATTEMPTS
        assert len(starts) == session.provision_attempts

        if outcome is ProvisionOutcome.READY:
            assert session.status is SessionStatus.READY
            assert len(live) == 1
            assert session.sandbox_ref == live[0].sandbox_id
        elif outcome is ProvisionOutcome.RETRY_SCHEDULED:
            assert session.status is SessionStatus.PROVISIONING
            assert live == ()
            assert session.next_provision_at == clock.now() + _MIN_INTERVAL
            assert session.provision_attempts < _MAX_ATTEMPTS
        elif outcome is ProvisionOutcome.FAILED:
            assert session.status is SessionStatus.FAILED
            assert live == ()
            assert session.provision_attempts == _MAX_ATTEMPTS

    # Consecutive attempts are spaced by at least LAB_PROVISION_MIN_INTERVAL (25.1/25.2).
    # A start is recorded after any provider time was spent, so the previous failure's
    # scheduling instant (>= its start) bounds the gap from below.
    for earlier, later in zip(starts, starts[1:], strict=False):
        assert later - earlier >= _MIN_INTERVAL

    # Once ready or failed, further deliveries never provision again (25.3).
    final = sessions.get("s1")
    assert final is not None
    if final.status in (SessionStatus.READY, SessionStatus.FAILED):
        before = len(starts)
        clock.advance(timedelta(seconds=60))
        assert job.execute("s1") is ProvisionOutcome.SKIPPED
        assert len(starts) == before
