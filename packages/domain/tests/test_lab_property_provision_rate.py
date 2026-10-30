"""Property test for the provisioning rate limit and retry spacing (task 1.9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain import config
from edu_it_domain.labs import session_rules as rules
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.session_rules import ProvisionRetry
from hypothesis import given, settings
from hypothesis import strategies as st

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
EPS = timedelta(microseconds=1)
LIMIT = config.LAB_PROVISION_RATE_LIMIT
WINDOW = config.LAB_PROVISION_RATE_WINDOW
MIN_INTERVAL = config.LAB_PROVISION_MIN_INTERVAL
MAX_ATTEMPTS = 1 + config.LAB_PROVISION_MAX_RETRIES


def _session(**kw: object) -> LabSession:
    base: dict[str, object] = {
        "stable_id": "s1",
        "user_id": "u1",
        "lab_id": "l1",
        "status": SessionStatus.PROVISIONING,
        "created_at": T0,
        "ttl_at": T0 + config.LAB_SESSION_TTL,
    }
    base.update(kw)
    return LabSession(**base)  # type: ignore[arg-type]


@settings(max_examples=100)
@given(
    now_offset=st.integers(min_value=0, max_value=3 * int(WINDOW.total_seconds())),
    ages=st.lists(st.integers(min_value=0, max_value=2 * int(WINDOW.total_seconds())), max_size=20),
)
def test_property_provision_rate_limit(now_offset: int, ages: list[int]) -> None:
    # Feature: hands-on-labs, Property 9: Provisioning rate limit
    # **Validates: Requirements 1.6, 7.5, 30.3**
    now = T0 + timedelta(seconds=now_offset)
    starts = [now - timedelta(seconds=a) for a in ages]
    counted = sorted(s for s in starts if now - s < WINDOW)

    decision = rules.provision_rate_decision(starts, now, config)
    assert decision.allowed == (len(counted) < LIMIT)

    if decision.allowed:
        assert decision.retry_after is None
        return

    # Rejection: positive retry_after equal to the time until the start that
    # blocks the next one (the oldest start that must leave) exits the window.
    assert decision.retry_after is not None
    assert decision.retry_after > timedelta(0)
    blocking = counted[len(counted) - LIMIT]
    assert decision.retry_after == blocking + WINDOW - now
    if len(counted) == LIMIT:
        assert blocking == counted[0]  # the oldest counted start

    retry_at = now + decision.retry_after
    assert rules.provision_rate_decision(starts, retry_at, config).allowed
    assert not rules.provision_rate_decision(starts, retry_at - EPS, config).allowed


@settings(max_examples=100)
@given(
    attempts=st.integers(min_value=0, max_value=MAX_ATTEMPTS + 3),
    delta=st.integers(min_value=-2 * int(MIN_INTERVAL.total_seconds()) - 1, max_value=600),
)
def test_property_provision_retry_spacing_and_cap(attempts: int, delta: int) -> None:
    # Feature: hands-on-labs, Property 9: Provisioning rate limit
    # **Validates: Requirements 1.6, 7.5, 30.3**
    next_at = T0 + MIN_INTERVAL
    session = _session(
        provision_attempts=attempts,
        next_provision_at=next_at if attempts > 0 else None,
    )
    now = next_at + timedelta(seconds=delta)
    result = rules.provision_retry_decision(session, now, config)

    if attempts >= MAX_ATTEMPTS:
        assert result is ProvisionRetry.FAIL
    elif attempts == 0 or now >= next_at:
        assert result is ProvisionRetry.ATTEMPT
    else:
        assert result is ProvisionRetry.WAIT
