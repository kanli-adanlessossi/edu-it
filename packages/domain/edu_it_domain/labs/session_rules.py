"""Pure Lab_Session lifetime, grading, and provisioning rules.

Covers hands-on-labs Requirements 1.7, 6.4, 7.2-7.6, 7.8, 11.1, 25.1, 27.2,
27.5, 27.7, 30.3 and 30.4.

Time enters only as an aware ``now`` instant that the caller reads from the
injected ``Clock`` (``clock.now()``); nothing here reads the wall clock. Every
business number is read from ``config`` (a mapping or any object exposing the
Config keys as attributes, for example ``edu_it_domain.config``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus

__all__ = [
    "ActivityEvent",
    "ActivityKind",
    "Deadlines",
    "Eligible",
    "ProvisionRetry",
    "RateDecision",
    "ReapAction",
    "Rejected",
    "RejectionReason",
    "deadlines",
    "grading_eligibility",
    "last_activity",
    "provision_rate_decision",
    "provision_retry_decision",
    "reap_decision",
]


def _cfg(config: object, key: str) -> object:
    if isinstance(config, Mapping):
        return config[key]
    return getattr(config, key)


def _cfg_td(config: object, key: str) -> timedelta:
    value = _cfg(config, key)
    if not isinstance(value, timedelta):
        raise TypeError(f"Config {key} must be a timedelta")
    return value


def _cfg_int(config: object, key: str) -> int:
    value = _cfg(config, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Config {key} must be an int")
    return value


# --- Activity and deadlines ---------------------------------------------------


class ActivityKind(Enum):
    """Kinds of event seen on a session; only learner-driven kinds are activity."""

    TERMINAL_INPUT = "terminal-input"
    FILE_SAVE = "file-save"
    GRADING_REQUEST = "grading-request"
    SANDBOX_OUTPUT = "sandbox-output"


_LEARNER_ACTIVITY: frozenset[ActivityKind] = frozenset(
    {ActivityKind.TERMINAL_INPUT, ActivityKind.FILE_SAVE, ActivityKind.GRADING_REQUEST}
)


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """One event on a session at an instant."""

    kind: ActivityKind
    at: datetime


def last_activity(events: Iterable[ActivityEvent], *, default: datetime) -> datetime:
    """Latest learner activity (Req 7.2).

    Terminal input, file saves and grading requests count; sandbox output does
    not. ``default`` (the session's creation or recorded last activity) is the
    floor, returned when there is no learner activity.
    """
    latest = default
    for event in events:
        if event.kind in _LEARNER_ACTIVITY and event.at > latest:
            latest = event.at
    return latest


@dataclass(frozen=True, slots=True)
class Deadlines:
    """The two instants that end a session, plus remaining-time helpers."""

    ttl_at: datetime
    idle_deadline: datetime

    @property
    def expires_at(self) -> datetime:
        """The earlier of the two deadlines."""
        return min(self.ttl_at, self.idle_deadline)

    def remaining_ttl(self, now: datetime) -> timedelta:
        return max(self.ttl_at - now, timedelta(0))

    def remaining_idle(self, now: datetime) -> timedelta:
        return max(self.idle_deadline - now, timedelta(0))

    def remaining(self, now: datetime) -> timedelta:
        return max(self.expires_at - now, timedelta(0))


def deadlines(session: LabSession, config: object) -> Deadlines:
    """Deadlines derived only from the TTL and the last learner activity (Req 6.4, 7.2)."""
    baseline = session.last_activity_at or session.created_at
    return Deadlines(
        ttl_at=session.ttl_at,
        idle_deadline=baseline + _cfg_td(config, "LAB_IDLE_TIMEOUT"),
    )


# --- Reaper -------------------------------------------------------------------


class ReapAction(Enum):
    """What the reaper does with one session."""

    KEEP = "keep"
    EXPIRE = "expire"
    IDLE_EXPIRE = "idle-expire"
    DEFER = "defer"
    DESTROY_ONLY = "destroy-only"


def reap_decision(
    session: LabSession,
    now: datetime,
    run: GradingRun | None,
    config: object,
) -> ReapAction:
    """Decide the reaper action for ``session`` (Req 7.2, 7.3, 27.2).

    ``DEFER`` while a run is ``running`` and ``now <= run.deadline_at``. After
    that deadline the run is closed by the reaper and the decision proceeds.
    A terminal session then only needs its Sandbox destroyed. ``EXPIRE`` only
    when ``now > ttl_at`` (kept at exactly ``ttl_at``); ``IDLE_EXPIRE`` when
    ``now >= idle deadline``.
    """
    if run is not None and run.status is RunStatus.RUNNING and now <= run.deadline_at:
        return ReapAction.DEFER
    if not session.status.is_active:
        return ReapAction.DESTROY_ONLY
    if now > session.ttl_at:
        return ReapAction.EXPIRE
    if now >= deadlines(session, config).idle_deadline:
        return ReapAction.IDLE_EXPIRE
    return ReapAction.KEEP


# --- Grading eligibility ------------------------------------------------------


class RejectionReason(Enum):
    """Why a grading request is rejected (maps to the API problem types)."""

    NOT_READY = "not-ready"
    IN_PROGRESS = "in-progress"
    EXPIRED = "expired"
    THROTTLED = "throttled"


@dataclass(frozen=True, slots=True)
class Eligible:
    """The grading request may be accepted."""


@dataclass(frozen=True, slots=True)
class Rejected:
    """The grading request is rejected; ``retry_after`` is set when throttled."""

    reason: RejectionReason
    retry_after: timedelta | None = None


def grading_eligibility(session: LabSession, now: datetime, config: object) -> Eligible | Rejected:
    """Accept iff status is ``ready``, ``now <= ttl_at`` and the gap is > 10 s.

    A request at exactly ``ttl_at`` is accepted; strictly after is rejected. A
    gap of exactly the minimum interval is rejected (Req 7.4, 7.8, 11.1, 27.5,
    27.7, 30.4). Ownership is checked by the repository, not here.
    """
    status = session.status
    if status is SessionStatus.GRADING:
        return Rejected(RejectionReason.IN_PROGRESS)
    if status in (SessionStatus.EXPIRED, SessionStatus.IDLE_EXPIRED):
        return Rejected(RejectionReason.EXPIRED)
    if status is not SessionStatus.READY:
        return Rejected(RejectionReason.NOT_READY)
    if now > session.ttl_at:
        return Rejected(RejectionReason.EXPIRED)
    last = session.last_grading_accepted_at
    if last is not None:
        interval = _cfg_td(config, "LAB_GRADING_MIN_INTERVAL")
        if not now - last > interval:
            return Rejected(RejectionReason.THROTTLED, retry_after=last + interval - now)
    return Eligible()


# --- Provisioning -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateDecision:
    """Outcome of the per-learner start rate limit; ``retry_after`` when denied."""

    allowed: bool
    retry_after: timedelta | None = None


def provision_rate_decision(
    starts: Sequence[datetime], now: datetime, config: object
) -> RateDecision:
    """Allowed iff fewer than the limit of starts in the trailing window (Req 1.7, 30.3).

    A start counts while ``now - start < window``. ``retry_after`` is the time
    until enough starts leave the window for one more to be allowed.
    """
    limit = _cfg_int(config, "LAB_PROVISION_RATE_LIMIT")
    window = _cfg_td(config, "LAB_PROVISION_RATE_WINDOW")
    recent = sorted(s for s in starts if now - s < window)
    if len(recent) < limit:
        return RateDecision(allowed=True)
    blocking = recent[len(recent) - limit]
    return RateDecision(allowed=False, retry_after=blocking + window - now)


class ProvisionRetry(Enum):
    """What to do next for a session whose provisioning has been attempted."""

    ATTEMPT = "attempt"
    WAIT = "wait"
    FAIL = "fail"


def provision_retry_decision(session: LabSession, now: datetime, config: object) -> ProvisionRetry:
    """Next provisioning attempt rule (Req 25.1, 7.6).

    ``FAIL`` once attempts reach ``1 + LAB_PROVISION_MAX_RETRIES``; otherwise
    ``ATTEMPT`` when no spacing is pending or ``now >= next_provision_at``
    (last attempt plus the minimum interval); else ``WAIT``.
    """
    max_attempts = 1 + _cfg_int(config, "LAB_PROVISION_MAX_RETRIES")
    if session.provision_attempts >= max_attempts:
        return ProvisionRetry.FAIL
    next_at = session.next_provision_at
    if session.provision_attempts == 0 or next_at is None or now >= next_at:
        return ProvisionRetry.ATTEMPT
    return ProvisionRetry.WAIT
