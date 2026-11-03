"""The ``GenerationLedger`` port (ai-content Req 1.4, 10.1, 12.1, 12.3).

One ``generation_job`` per Generation_Run and one ``budget_ledger`` row per model
attempt, failed attempts included. ``record_attempt`` writes the ledger row and
updates the job totals as one atomic operation, so the totals always equal the sum
of the rows. The spend queries feed the budget gate (Req 10.1).

Neither table holds personal data. Jobs are addressed by ``stable_id``; internal
keys never cross this port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from edu_it_domain.ports.llm_provider import AttemptUsage

__all__ = [
    "GenerationLedger",
    "JobKind",
    "JobRef",
    "JobStatus",
    "NewJob",
]


class JobKind(StrEnum):
    """What a job was opened for."""

    STAGE = "stage"
    REFILL = "refill"
    ADVISOR = "advisor"
    EVAL = "eval"


class JobStatus(StrEnum):
    """Lifecycle status of a job."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_REACHED = "budget_reached"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class NewJob:
    """Fields needed to open a job."""

    stable_id: str
    kind: JobKind
    track_stable_id: str | None = None
    stage: str | None = None
    unit_key: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    cost_cap_cents: int | None = None
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class JobRef:
    """Handle to an opened job."""

    stable_id: str


@runtime_checkable
class GenerationLedger(Protocol):
    """Port recording jobs and per-attempt usage, and reporting spend."""

    def open_job(self, job: NewJob) -> JobRef:
        """Create a ``running`` job with zero totals.

        Raises ``ValueError`` if ``job.stable_id`` already exists.
        """
        ...

    def record_attempt(self, job: JobRef, usage: AttemptUsage) -> None:
        """Append one ledger row and add the attempt to the job totals, atomically.

        ``attempt_no`` is the job's previous row count plus one. The row carries
        the job's track. Raises ``ValueError`` for an unknown job; nothing is
        written in that case.
        """
        ...

    def close_job(self, job: JobRef, status: JobStatus) -> None:
        """Set the job status and ``finished_at``. Raises ``ValueError`` if unknown."""
        ...

    def spent_today(self, day_start: datetime) -> int:
        """Cents of all ledger rows recorded at or after ``day_start``, across jobs."""
        ...

    def spent_for_track(self, track_stable_id: str) -> int:
        """Cents of all ledger rows carrying ``track_stable_id``."""
        ...

    def spent_for_job(self, job: JobRef) -> int:
        """Cents recorded for ``job`` (0 for an unknown job)."""
        ...
