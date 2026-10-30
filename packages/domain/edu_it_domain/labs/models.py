"""Framework-free lab domain models (hands-on-labs Req 3.1-3.5, 11.4, 11.8).

Frozen, slotted dataclasses and enums only; standard library imports only.
Instants are aware ``datetime`` values read from the injected ``Clock``.

``CheckResult`` is the only per-Check record that is persisted and shown to the
learner. It deliberately has no field for output, exit codes, or paths
(Req 11.4): the worker maps those to an outcome and discards them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

type StableId = str


class SessionStatus(Enum):
    """Persisted Lab_Session status; values are the stored strings."""

    PROVISIONING = "provisioning"
    READY = "ready"
    GRADING = "grading"
    ENDED = "ended"
    EXPIRED = "expired"
    IDLE_EXPIRED = "idle-expired"
    FAILED = "failed"

    @property
    def is_active(self) -> bool:
        """Active_Session: provisioning, ready, or grading."""

        return self in ACTIVE_SESSION_STATUSES


ACTIVE_SESSION_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.PROVISIONING, SessionStatus.READY, SessionStatus.GRADING}
)


class RunStatus(Enum):
    """Persisted grading run status; values are the stored strings."""

    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    COULD_NOT_COMPLETE = "could-not-complete"


class CheckOutcome(Enum):
    """Outcome of one Check. Note the underscore form, per the data model."""

    PASSED = "passed"
    FAILED = "failed"
    COULD_NOT_COMPLETE = "could_not_complete"


@dataclass(frozen=True, slots=True)
class LabSession:
    """A learner's sandboxed lab session (D3 ``lab_session``)."""

    stable_id: StableId
    user_id: StableId
    lab_id: StableId
    status: SessionStatus
    created_at: datetime
    ttl_at: datetime
    sandbox_ref: str | None = None
    last_activity_at: datetime | None = None
    last_grading_accepted_at: datetime | None = None
    ended_at: datetime | None = None
    provision_attempts: int = 0
    next_provision_at: datetime | None = None
    terminal_epoch: int = 0


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The sanitized result of one Check: ids, outcome and a feedback key only."""

    check_id: StableId
    task_id: StableId
    outcome: CheckOutcome
    outcome_code: str
    feedback_key: str


@dataclass(frozen=True, slots=True)
class GradingRun:
    """One grading run (D3 ``lab_run``).

    ``deadline_at`` is ``accepted_at`` plus the run deadline; ``started_at`` is
    when the Grading_Job began (``None`` while queued), so queue delay is
    ``started_at - accepted_at`` (Req 11.8).
    """

    stable_id: StableId
    session_id: StableId
    status: RunStatus
    accepted_at: datetime
    deadline_at: datetime
    started_at: datetime | None = None
    graded_at: datetime | None = None
    results: tuple[CheckResult, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskCompletion:
    """A learner's completion of a lab task (D3 ``lab_task_completion``)."""

    user_id: StableId
    task_id: StableId
    run_id: StableId
    completed_at: datetime
    counted_for_mastery: bool


@dataclass(frozen=True, slots=True)
class WritableMounts:
    """Byte sizes of the size-limited tmpfs mounts; they sum to the quota."""

    workspace: int
    home: int
    tmp: int
    grader: int

    @property
    def total(self) -> int:
        return self.workspace + self.home + self.tmp + self.grader


@dataclass(frozen=True, slots=True)
class TmpfsMount:
    """One size-limited tmpfs mount."""

    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Everything a provider needs to create a Sandbox; hardened by construction."""

    session_stable_id: StableId
    image: str
    user: str
    network_enabled: bool
    cpu_limit: int
    memory_limit_bytes: int
    pid_limit: int
    writable_quota_bytes: int
    drop_all_capabilities: bool
    no_new_privileges: bool
    read_only_root: bool
    host_mounts: tuple[str, ...]
    tmpfs_mounts: tuple[TmpfsMount, ...]
    labels: tuple[tuple[str, str], ...] = ()
