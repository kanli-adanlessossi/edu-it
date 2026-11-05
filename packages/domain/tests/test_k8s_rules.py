"""Unit tests for the pure Kubernetes rules (kubernetes-deployment Req 2.9-2.10, 6.6-6.7,
11.9-11.12, 14.5, 19.2-19.5, 19.7-19.8, 20.1, 20.3, 20.7, 22.6-22.7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.k8s_rules import (
    LearnerLabStats,
    capacity_decision,
    cni_check_outcome,
    infra_loss_transition,
    reconnect_delays,
    runtime_guard,
    select_reaper_actions,
)
from edu_it_domain.labs.k8s_types import CniOutcome, Fail, Requeue, RuntimeDecision
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.session_rules import ReapAction
from edu_it_domain.ports.sandbox import SandboxHandle

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
VECTORS = Path(__file__).resolve().parents[3] / "tests" / "shared" / "reconnect-vectors.json"


def _session(
    sid: str,
    status: SessionStatus = SessionStatus.READY,
    *,
    ttl: timedelta = timedelta(minutes=30),
    activity: timedelta = timedelta(minutes=1),
    ref: str | None = "p",
) -> LabSession:
    return LabSession(
        stable_id=sid,
        user_id="u",
        lab_id="l",
        status=status,
        created_at=NOW - timedelta(minutes=5),
        ttl_at=NOW + ttl,
        sandbox_ref=ref,
        last_activity_at=NOW - activity,
    )


def _pod(sid: str) -> SandboxHandle:
    return SandboxHandle(sandbox_id=f"pod-{sid}", session_stable_id=sid, provider="kubernetes")


def test_capacity_decision() -> None:
    t, b = timedelta(seconds=30), timedelta(seconds=10)
    assert capacity_decision(timedelta(seconds=29), t, b) == Requeue(delay=b)
    assert capacity_decision(t, t, b) == Fail(reason="capacity")


@pytest.mark.parametrize(
    ("kind", "env", "expected"),
    [
        ("runc", "development", RuntimeDecision.WARN),
        ("runc", "production", RuntimeDecision.REFUSE),
        ("gvisor", "production", RuntimeDecision.OK),
        ("kata", "development", RuntimeDecision.OK),
    ],
)
def test_runtime_guard(kind: str, env: str, expected: RuntimeDecision) -> None:
    assert runtime_guard(kind, env) is expected


def test_cni_check_outcome() -> None:
    assert cni_check_outcome(True, True, False, True) is CniOutcome.PASS
    assert cni_check_outcome(True, False, False, True) is CniOutcome.FAIL
    assert cni_check_outcome(True, True, True, False) is CniOutcome.WARN


@pytest.mark.parametrize("kind", ["drain", "restart"])
def test_reconnect_delays_match_shared_vectors(kind: str) -> None:
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    got = [d.total_seconds() for d in reconnect_delays(kind)]  # type: ignore[arg-type]
    assert got == vectors[kind]["delays_seconds"]


def test_restart_schedule_total_is_bounded() -> None:
    delays = reconnect_delays("restart")
    assert max(delays) <= timedelta(seconds=5)
    assert sum(delays, timedelta(0)) <= timedelta(seconds=60)


def test_unknown_reconnect_kind_rejected() -> None:
    with pytest.raises(ValueError):
        reconnect_delays("other")  # type: ignore[arg-type]


def test_infra_loss_transition_is_idempotent_and_keeps_stats() -> None:
    session = _session("s1")
    stats = LearnerLabStats("s1", 2, 0.4)
    first = infra_loss_transition(session, stats, NOW)
    assert first.session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert first.counter_delta == 1
    assert first.learner_stats == LearnerLabStats(None, 2, 0.4)
    again = infra_loss_transition(first.session, stats, NOW)
    assert again.counter_delta == 0
    assert again.learner_stats.active_session_id is None


def test_infra_loss_ignores_other_terminal_and_foreign_slot() -> None:
    ended = _session("s1", SessionStatus.ENDED)
    stats = LearnerLabStats("s1", 0, 0.0)
    assert infra_loss_transition(ended, stats, NOW).counter_delta == 0
    other = infra_loss_transition(_session("s2"), stats, NOW)
    assert other.learner_stats.active_session_id == "s1"


def test_reaper_never_selects_live_session_pods() -> None:
    sessions = [
        _session("live"),
        _session("old", ttl=timedelta(minutes=-1)),
        _session("done", SessionStatus.ENDED),
    ]
    pods = [_pod("live"), _pod("old"), _pod("done"), _pod("orphan")]
    actions = select_reaper_actions(sessions, pods, NOW)
    assert {p.session_stable_id for p in actions.delete_pods} == {"old", "done", "orphan"}
    assert actions.expire_sessions == (("old", ReapAction.EXPIRE),)
    assert actions.lost_sessions == ()


def test_reaper_reports_lost_and_deletes_failed_pod_of_ended_session() -> None:
    sessions = [
        _session("gone"),
        _session("failed"),
        _session("evicted", SessionStatus.ENDED_BY_INFRASTRUCTURE),
    ]
    pods = [_pod("failed"), _pod("evicted")]
    actions = select_reaper_actions(
        sessions, pods, NOW, failed_pod_ids=frozenset({"pod-failed", "pod-evicted"})
    )
    assert actions.lost_sessions == ("failed", "gone")
    assert [p.session_stable_id for p in actions.delete_pods] == ["evicted"]


def test_reaper_is_stateless() -> None:
    sessions = [_session("a"), _session("b", ttl=timedelta(minutes=-1))]
    pods = [_pod("b"), _pod("z")]
    assert select_reaper_actions(sessions, pods, NOW) == select_reaper_actions(sessions, pods, NOW)
