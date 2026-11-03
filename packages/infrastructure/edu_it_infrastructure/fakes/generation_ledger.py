"""In-memory ``GenerationLedger`` (ai-content task 3.2).

Honors the port semantics without a database: ``record_attempt`` appends the row
and updates the totals under one lock, so they never drift.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from edu_it_domain.ports.generation_ledger import JobRef, JobStatus

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.generation_ledger import NewJob
    from edu_it_domain.ports.llm_provider import AttemptUsage

__all__ = ["In_Memory_GenerationLedger"]


@dataclass
class _Row:
    attempt_no: int
    usage: AttemptUsage
    track_stable_id: str | None
    recorded_at: datetime


@dataclass
class _Job:
    spec: NewJob
    status: JobStatus = JobStatus.RUNNING
    tokens: int = 0
    cost_cents: int = 0
    finished_at: datetime | None = None
    rows: list[_Row] = field(default_factory=list)


def _utc(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


class In_Memory_GenerationLedger:
    """In-memory ``GenerationLedger``."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.RLock()

    def open_job(self, job: NewJob) -> JobRef:
        with self._lock:
            if job.stable_id in self._jobs:
                raise ValueError(f"job exists: {job.stable_id}")
            self._jobs[job.stable_id] = _Job(job)
        return JobRef(job.stable_id)

    def _job(self, job: JobRef) -> _Job:
        found = self._jobs.get(job.stable_id)
        if found is None:
            raise ValueError(f"unknown job {job.stable_id!r}")
        return found

    def record_attempt(self, job: JobRef, usage: AttemptUsage) -> None:
        with self._lock:
            entry = self._job(job)
            entry.rows.append(
                _Row(
                    len(entry.rows) + 1,
                    usage,
                    entry.spec.track_stable_id,
                    _utc(self._clock.now()),
                )
            )
            entry.tokens += usage.input_tokens + usage.output_tokens
            entry.cost_cents += usage.cost_cents

    def close_job(self, job: JobRef, status: JobStatus) -> None:
        with self._lock:
            entry = self._job(job)
            entry.status = status
            entry.finished_at = _utc(self._clock.now())

    def spent_today(self, day_start: datetime) -> int:
        start = _utc(day_start)
        with self._lock:
            return sum(
                r.usage.cost_cents
                for j in self._jobs.values()
                for r in j.rows
                if r.recorded_at >= start
            )

    def spent_for_track(self, track_stable_id: str) -> int:
        with self._lock:
            return sum(
                r.usage.cost_cents
                for j in self._jobs.values()
                for r in j.rows
                if r.track_stable_id == track_stable_id
            )

    def spent_for_job(self, job: JobRef) -> int:
        with self._lock:
            found = self._jobs.get(job.stable_id)
            return 0 if found is None else sum(r.usage.cost_cents for r in found.rows)

    # --- inspection helpers for tests (not part of the port) ---------------

    def job_totals(self, job: JobRef) -> tuple[int, int]:
        """Return ``(tokens, cost_cents)`` of the job totals."""
        entry = self._job(job)
        return entry.tokens, entry.cost_cents
