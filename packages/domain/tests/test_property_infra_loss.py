"""Property 15: Infrastructure loss transition.

Feature: kubernetes-deployment, Property 15: Infrastructure loss transition

# Claims: 19.2, 19.3, 19.4, 19.7, 19.8

**Validates: Requirements 19.2, 19.3, 19.4, 19.7, 19.8**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.labs.k8s_rules import LearnerLabStats, infra_loss_transition
from edu_it_domain.labs.models import ACTIVE_SESSION_STATUSES, LabSession, SessionStatus
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

_active_statuses = st.sampled_from(sorted(ACTIVE_SESSION_STATUSES, key=lambda s: s.value))


def _session(status: SessionStatus) -> LabSession:
    return LabSession(
        stable_id="s1",
        user_id="u1",
        lab_id="l1",
        status=status,
        created_at=NOW - timedelta(minutes=5),
        ttl_at=NOW + timedelta(minutes=30),
        sandbox_ref="pod-s1",
        last_activity_at=NOW - timedelta(minutes=1),
    )


_stats = st.builds(
    LearnerLabStats,
    active_session_id=st.sampled_from([None, "s1"]),
    grading_attempts_used=st.integers(min_value=0, max_value=100),
    mastery_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)


@settings(max_examples=100, deadline=None)
@given(
    status=_active_statuses,
    stats=_stats,
    repeats=st.integers(min_value=1, max_value=6),
    later=st.integers(min_value=0, max_value=3600),
)
def test_infra_loss_ends_session_frees_slot_and_counts_once(
    status: SessionStatus, stats: LearnerLabStats, repeats: int, later: int
) -> None:
    session = _session(status)
    first = infra_loss_transition(session, stats, NOW)

    assert first.session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert first.session.ended_at == NOW
    assert first.counter_delta == 1
    assert first.learner_stats.active_session_id is None
    assert first.learner_stats.grading_attempts_used == stats.grading_attempts_used
    assert first.learner_stats.mastery_score == stats.mastery_score

    current, current_stats = first.session, first.learner_stats
    total = first.counter_delta
    for i in range(repeats):
        again = infra_loss_transition(current, current_stats, NOW + timedelta(seconds=later + i))
        assert again.session == first.session
        assert again.learner_stats == first.learner_stats
        total += again.counter_delta
        current, current_stats = again.session, again.learner_stats

    assert total == 1


@settings(max_examples=100, deadline=None)
@given(
    status=_active_statuses,
    other_id=st.sampled_from(["s2", "other"]),
    attempts=st.integers(min_value=0, max_value=100),
)
def test_infra_loss_keeps_slot_held_by_another_session(
    status: SessionStatus, other_id: str, attempts: int
) -> None:
    stats = LearnerLabStats(other_id, attempts, 0.5)
    result = infra_loss_transition(_session(status), stats, NOW)
    assert result.session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert result.learner_stats == stats
    assert result.counter_delta == 1
