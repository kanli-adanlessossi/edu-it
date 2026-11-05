"""Pure decision rules of the Kubernetes deployment (kubernetes-deployment Req 2.9, 2.10,
6.6, 6.7, 11.9-11.12, 14.5, 19.2-19.5, 19.7, 19.8, 20.1, 20.3, 20.7, 22.6, 22.7).

Standard library imports only. Time enters as an aware ``now`` instant read from the
injected ``Clock``; every business number comes from ``config``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from edu_it_domain import config as _default_config
from edu_it_domain.labs.k8s_types import (
    CapacityDecision,
    CniOutcome,
    Fail,
    Requeue,
    RuntimeDecision,
)
from edu_it_domain.labs.models import (
    GradingRun,
    LabSession,
    SessionStatus,
    StableId,
)
from edu_it_domain.labs.session_rules import ReapAction, reap_decision
from edu_it_domain.ports.sandbox import SandboxHandle

__all__ = [
    "InfraLossResult",
    "LearnerLabStats",
    "ReaperActions",
    "ReconnectKind",
    "capacity_decision",
    "cni_check_outcome",
    "infra_loss_transition",
    "reconnect_delays",
    "runtime_guard",
    "select_reaper_actions",
]

# --- Capacity (Req 22.6, 22.7) -------------------------------------------------


def capacity_decision(
    elapsed: timedelta, provision_timeout: timedelta, backoff: timedelta
) -> CapacityDecision:
    """Requeue with ``backoff`` while ``elapsed`` is below the timeout, else fail."""
    if elapsed < provision_timeout:
        return Requeue(delay=backoff)
    return Fail(reason="capacity")


# --- Runtime guard (Req 2.9, 2.10) ----------------------------------------------


def runtime_guard(runtime_kind: str, environment: str) -> RuntimeDecision:
    """``runc`` warns outside production and is refused in production; others are ok."""
    if runtime_kind != "runc":
        return RuntimeDecision.OK
    if environment == "production":
        return RuntimeDecision.REFUSE
    return RuntimeDecision.WARN


# --- CNI check (Req 11.9-11.12) -------------------------------------------------


def cni_check_outcome(
    allowed_reachable: bool, denied_blocked: bool, timed_out: bool, required: bool
) -> CniOutcome:
    """Pass only when enforcement is proven; otherwise fail if required, else warn."""
    if allowed_reachable and denied_blocked and not timed_out:
        return CniOutcome.PASS
    return CniOutcome.FAIL if required else CniOutcome.WARN


# --- Reconnect schedules (Req 14.5, 20.1) ----------------------------------------

type ReconnectKind = Literal["drain", "restart"]


def reconnect_delays(kind: ReconnectKind) -> tuple[timedelta, ...]:
    """Delays between reconnect attempts for the ``drain`` or ``restart`` schedule.

    ``drain`` is the configured tuple. ``restart`` repeats its last delay while the
    running total stays within ``TERMINAL_RESTART_RECONNECT_TOTAL``.
    """
    cfg = _default_config
    if kind == "drain":
        return cfg.TERMINAL_DRAIN_RECONNECT_DELAYS[: cfg.TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS]
    if kind != "restart":
        raise ValueError(f"unknown reconnect schedule: {kind!r}")
    base = cfg.TERMINAL_RESTART_RECONNECT_DELAYS
    total = cfg.TERMINAL_RESTART_RECONNECT_TOTAL
    delays: list[timedelta] = []
    spent = timedelta(0)
    index = 0
    while True:
        delay = base[min(index, len(base) - 1)]
        if spent + delay > total:
            break
        delays.append(delay)
        spent += delay
        index += 1
    return tuple(delays)


# --- Infrastructure loss (Req 19.2-19.4, 19.7, 19.8) --------------------------------


@dataclass(frozen=True, slots=True)
class LearnerLabStats:
    """The learner's lab state that an infrastructure loss must not disturb."""

    active_session_id: StableId | None
    grading_attempts_used: int
    mastery_score: float


@dataclass(frozen=True, slots=True)
class InfraLossResult:
    """New session and learner state; ``counter_delta`` feeds the loss metric."""

    session: LabSession
    learner_stats: LearnerLabStats
    counter_delta: int


def infra_loss_transition(
    session: LabSession, learner_stats: LearnerLabStats, now: datetime
) -> InfraLossResult:
    """End an active session by infrastructure loss.

    Sets ``ended-by-infrastructure``, frees the learner's active-session slot when it
    holds this session, and keeps attempts and mastery. ``counter_delta`` is 1 only on
    the first transition; repeats and sessions already ended otherwise give 0.
    """
    if session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE:
        return InfraLossResult(session, _release_slot(learner_stats, session), 0)
    if not session.status.is_active:
        return InfraLossResult(session, learner_stats, 0)
    ended = replace(session, status=SessionStatus.ENDED_BY_INFRASTRUCTURE, ended_at=now)
    return InfraLossResult(ended, _release_slot(learner_stats, session), 1)


def _release_slot(stats: LearnerLabStats, session: LabSession) -> LearnerLabStats:
    if stats.active_session_id == session.stable_id:
        return replace(stats, active_session_id=None)
    return stats


# --- Reaper (Req 6.6, 6.7, 19.5, 20.3, 20.7) ----------------------------------------


@dataclass(frozen=True, slots=True)
class ReaperActions:
    """What one reaper tick does; every field is sorted so the result is deterministic."""

    delete_pods: tuple[SandboxHandle, ...]
    """Pods to delete: expired sessions, orphans, evicted or failed pods."""

    expire_sessions: tuple[tuple[StableId, ReapAction], ...]
    """Active sessions past their TTL or idle deadline, with the status to set."""

    lost_sessions: tuple[StableId, ...]
    """Active sessions whose pod is gone or failed: run ``infra_loss_transition``."""


def select_reaper_actions(
    sessions: Sequence[LabSession],
    pods: Sequence[SandboxHandle],
    now: datetime,
    *,
    failed_pod_ids: frozenset[str] = frozenset(),
    runs: Mapping[StableId, GradingRun] | None = None,
    config: object = _default_config,
) -> ReaperActions:
    """Select reaper actions from persisted rows and the provider listing only.

    A pod of a live session (decision ``KEEP`` or ``DEFER``) is never deleted. A live
    session that has a pod but whose pod failed or was evicted, or that has no pod
    after leaving provisioning, is reported as lost; its pod is removed on the next
    tick, once the session is no longer active.
    """
    runs = runs or {}
    by_id = {s.stable_id: s for s in sessions}
    decisions = {
        s.stable_id: reap_decision(s, now, runs.get(s.stable_id), config) for s in sessions
    }
    live = {sid for sid, d in decisions.items() if d in (ReapAction.KEEP, ReapAction.DEFER)}

    pod_sessions = {p.session_stable_id for p in pods}
    delete: list[SandboxHandle] = []
    for pod in pods:
        sid = pod.session_stable_id
        if sid in by_id and sid in live:
            continue
        delete.append(pod)  # expired, terminal (incl. evicted/failed) or orphaned

    expire = [
        (sid, d) for sid, d in decisions.items() if d in (ReapAction.EXPIRE, ReapAction.IDLE_EXPIRE)
    ]

    failed_sessions = {p.session_stable_id for p in pods if p.sandbox_id in failed_pod_ids}
    lost = [
        sid
        for sid in live
        if by_id[sid].status in (SessionStatus.READY, SessionStatus.GRADING)
        and by_id[sid].sandbox_ref is not None
        and (sid not in pod_sessions or sid in failed_sessions)
    ]

    return ReaperActions(
        delete_pods=tuple(sorted(delete, key=lambda p: (p.session_stable_id, p.sandbox_id))),
        expire_sessions=tuple(sorted(expire, key=lambda e: e[0])),
        lost_sessions=tuple(sorted(lost)),
    )
