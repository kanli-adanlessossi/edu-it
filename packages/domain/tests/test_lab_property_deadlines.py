"""Property test for lab session deadlines (task 1.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain import config
from edu_it_domain.labs import session_rules as rules
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.session_rules import ActivityEvent, ActivityKind
from hypothesis import given, settings
from hypothesis import strategies as st

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
TTL = config.LAB_SESSION_TTL
IDLE = config.LAB_IDLE_TIMEOUT

_LEARNER_KINDS = (
    ActivityKind.TERMINAL_INPUT,
    ActivityKind.FILE_SAVE,
    ActivityKind.GRADING_REQUEST,
)

_offsets = st.integers(min_value=0, max_value=7200).map(lambda s: T0 + timedelta(seconds=s))
_learner_events = st.lists(
    st.builds(ActivityEvent, kind=st.sampled_from(_LEARNER_KINDS), at=_offsets), max_size=10
)
_output_events = st.lists(
    st.builds(ActivityEvent, kind=st.just(ActivityKind.SANDBOX_OUTPUT), at=_offsets), max_size=10
)


def _session(last_activity_at: datetime) -> LabSession:
    return LabSession(
        stable_id="s1",
        user_id="u1",
        lab_id="l1",
        status=SessionStatus.READY,
        created_at=T0,
        ttl_at=T0 + TTL,
        last_activity_at=last_activity_at,
    )


@settings(max_examples=100)
@given(
    learner=_learner_events,
    output=_output_events,
    now_offset=st.integers(min_value=0, max_value=7200),
    other_now_offset=st.integers(min_value=0, max_value=7200),
)
def test_property_deadlines_depend_only_on_activity_and_ttl(
    learner: list[ActivityEvent],
    output: list[ActivityEvent],
    now_offset: int,
    other_now_offset: int,
) -> None:
    # Feature: hands-on-labs, Property 4: Deadlines are derived from learner activity
    # and the TTL only
    # **Validates: Requirements 6.4, 7.2, 12.6**
    last = rules.last_activity(learner, default=T0)
    # Sandbox output at any instant never moves the last activity.
    assert rules.last_activity([*learner, *output], default=T0) == last
    assert rules.last_activity(output, default=T0) == T0

    d = rules.deadlines(_session(last), config)
    assert d.ttl_at == T0 + TTL
    assert d.idle_deadline == last + IDLE

    # Deadlines with sandbox output mixed in are identical.
    mixed = rules.last_activity([*output, *learner], default=T0)
    assert rules.deadlines(_session(mixed), config) == d

    # Deadlines are fixed instants; only the remaining time depends on ``now``.
    now = T0 + timedelta(seconds=now_offset)
    other = T0 + timedelta(seconds=other_now_offset)
    assert rules.deadlines(_session(last), config) == d
    assert d.remaining(now) == max(min(d.ttl_at, d.idle_deadline) - now, timedelta(0))
    assert d.remaining(other) == max(min(d.ttl_at, d.idle_deadline) - other, timedelta(0))
