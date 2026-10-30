"""Unit and property tests for ``labs/session_rules.py`` (task 1.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain import config
from edu_it_domain.labs import session_rules as rules
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.labs.session_rules import (
    ActivityEvent,
    ActivityKind,
    Eligible,
    ProvisionRetry,
    ReapAction,
    Rejected,
    RejectionReason,
)
from edu_it_domain.ports.clock import Clock

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
TTL = config.LAB_SESSION_TTL
IDLE = config.LAB_IDLE_TIMEOUT
GAP = config.LAB_GRADING_MIN_INTERVAL
EPS = timedelta(microseconds=1)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _session(**kw: object) -> LabSession:
    base: dict[str, object] = {
        "stable_id": "s1",
        "user_id": "u1",
        "lab_id": "l1",
        "status": SessionStatus.READY,
        "created_at": T0,
        "ttl_at": T0 + TTL,
    }
    base.update(kw)
    return LabSession(**base)  # type: ignore[arg-type]


def _run(status: RunStatus, accepted: datetime) -> GradingRun:
    return GradingRun(
        stable_id="r1",
        session_id="s1",
        status=status,
        accepted_at=accepted,
        deadline_at=accepted + config.LAB_GRADING_RUN_DEADLINE,
    )


def test_clock_port_supplies_now() -> None:
    clock: Clock = _Clock(T0)
    assert isinstance(clock, Clock)
    assert rules.reap_decision(_session(), clock.now(), None, config) is ReapAction.KEEP


# --- last_activity / deadlines ---------------------------------------------------


def test_sandbox_output_is_not_activity() -> None:
    events = [
        ActivityEvent(ActivityKind.TERMINAL_INPUT, T0 + timedelta(minutes=1)),
        ActivityEvent(ActivityKind.SANDBOX_OUTPUT, T0 + timedelta(minutes=9)),
        ActivityEvent(ActivityKind.FILE_SAVE, T0 + timedelta(minutes=2)),
    ]
    assert rules.last_activity(events, default=T0) == T0 + timedelta(minutes=2)
    assert rules.last_activity([], default=T0) == T0
    only_output = [ActivityEvent(ActivityKind.SANDBOX_OUTPUT, T0 + timedelta(minutes=5))]
    assert rules.last_activity(only_output, default=T0) == T0


def test_deadlines_from_activity_and_ttl() -> None:
    s = _session(last_activity_at=T0 + timedelta(minutes=10))
    d = rules.deadlines(s, config)
    assert d.ttl_at == T0 + TTL
    assert d.idle_deadline == T0 + timedelta(minutes=10) + IDLE
    assert d.remaining(T0 + timedelta(minutes=10)) == IDLE
    assert rules.deadlines(_session(), config).idle_deadline == T0 + IDLE


# --- reap_decision -----------------------------------------------------------------


def test_reap_ttl_boundary_is_strict() -> None:
    s = _session(last_activity_at=T0 + TTL)  # idle cannot trigger
    assert rules.reap_decision(s, s.ttl_at, None, config) is ReapAction.KEEP
    assert rules.reap_decision(s, s.ttl_at + EPS, None, config) is ReapAction.EXPIRE


def test_reap_idle_boundary_is_inclusive() -> None:
    s = _session()
    assert rules.reap_decision(s, T0 + IDLE - EPS, None, config) is ReapAction.KEEP
    assert rules.reap_decision(s, T0 + IDLE, None, config) is ReapAction.IDLE_EXPIRE


def test_reap_defers_while_run_inside_deadline() -> None:
    s = _session(status=SessionStatus.GRADING)
    run = _run(RunStatus.RUNNING, T0)
    assert rules.reap_decision(s, run.deadline_at, run, config) is ReapAction.DEFER
    assert rules.reap_decision(s, run.deadline_at + EPS, run, config) is ReapAction.KEEP
    ended = _session(status=SessionStatus.ENDED)
    assert rules.reap_decision(ended, T0, run, config) is ReapAction.DEFER
    done = _run(RunStatus.PASSED, T0)
    assert rules.reap_decision(ended, T0, done, config) is ReapAction.DESTROY_ONLY


def test_reap_terminal_sessions_destroy_only() -> None:
    for status in (
        SessionStatus.ENDED,
        SessionStatus.EXPIRED,
        SessionStatus.IDLE_EXPIRED,
        SessionStatus.FAILED,
    ):
        s = _session(status=status)
        assert rules.reap_decision(s, T0 + TTL * 2, None, config) is ReapAction.DESTROY_ONLY


# --- grading_eligibility ---------------------------------------------------------


def test_grading_gap_boundary() -> None:
    s = _session(last_grading_accepted_at=T0)
    at_gap = rules.grading_eligibility(s, T0 + GAP, config)
    assert at_gap == Rejected(RejectionReason.THROTTLED, retry_after=timedelta(0))
    just_over = rules.grading_eligibility(s, T0 + GAP + EPS, config)
    assert isinstance(just_over, Eligible)
    early = rules.grading_eligibility(s, T0 + timedelta(seconds=4), config)
    assert early == Rejected(RejectionReason.THROTTLED, retry_after=timedelta(seconds=6))


def test_grading_ttl_boundary() -> None:
    s = _session()
    assert isinstance(rules.grading_eligibility(s, s.ttl_at, config), Eligible)
    assert rules.grading_eligibility(s, s.ttl_at + EPS, config) == Rejected(RejectionReason.EXPIRED)


def test_grading_requires_ready_status() -> None:
    expect = {
        SessionStatus.PROVISIONING: RejectionReason.NOT_READY,
        SessionStatus.GRADING: RejectionReason.IN_PROGRESS,
        SessionStatus.ENDED: RejectionReason.NOT_READY,
        SessionStatus.FAILED: RejectionReason.NOT_READY,
        SessionStatus.EXPIRED: RejectionReason.EXPIRED,
        SessionStatus.IDLE_EXPIRED: RejectionReason.EXPIRED,
    }
    for status, reason in expect.items():
        res = rules.grading_eligibility(_session(status=status), T0, config)
        assert res == Rejected(reason)


# --- provisioning ----------------------------------------------------------------------


def test_rate_limit_example() -> None:
    now = T0 + timedelta(minutes=20)
    starts = [now - timedelta(minutes=m) for m in (9, 7, 5, 3, 1)]
    d = rules.provision_rate_decision(starts, now, config)
    assert not d.allowed and d.retry_after == timedelta(minutes=1)
    assert rules.provision_rate_decision(starts[1:], now, config).allowed
    # a start exactly one window old no longer counts
    edge = [now - config.LAB_PROVISION_RATE_WINDOW, *starts[1:]]
    assert rules.provision_rate_decision(edge, now, config).allowed


def test_provision_retry_decision() -> None:
    nxt = T0 + config.LAB_PROVISION_MIN_INTERVAL
    first = _session(status=SessionStatus.PROVISIONING)
    assert rules.provision_retry_decision(first, T0, config) is ProvisionRetry.ATTEMPT
    second = _session(
        status=SessionStatus.PROVISIONING, provision_attempts=1, next_provision_at=nxt
    )
    assert rules.provision_retry_decision(second, nxt - EPS, config) is ProvisionRetry.WAIT
    assert rules.provision_retry_decision(second, nxt, config) is ProvisionRetry.ATTEMPT
    third = _session(status=SessionStatus.PROVISIONING, provision_attempts=2, next_provision_at=nxt)
    assert rules.provision_retry_decision(third, nxt, config) is ProvisionRetry.ATTEMPT
    spent = _session(status=SessionStatus.PROVISIONING, provision_attempts=3, next_provision_at=nxt)
    assert rules.provision_retry_decision(spent, nxt + TTL, config) is ProvisionRetry.FAIL


def test_rules_read_numbers_from_mapping_config() -> None:
    cfg = {"LAB_IDLE_TIMEOUT": timedelta(minutes=1)}
    assert rules.deadlines(_session(), cfg).idle_deadline == T0 + timedelta(minutes=1)
