"""The ``Scheduler`` thread that enqueues the periodic lab jobs (Req 6.5, 8.1, 9.1).

Each periodic job fires once per interval-sized time bucket of the ``Clock``. The dedupe
key carries the bucket number (``<job>:tick:<n>``), so several worker replicas, or a
restart inside one bucket, enqueue the job once while it is pending. :meth:`tick` is the
whole behavior and is driven directly in tests with a ``Test_Clock``; :meth:`start` only
runs it on a polling thread.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta

    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.task_queue import TaskQueue

__all__ = ["PeriodicJob", "Scheduler", "tick_dedupe_key"]

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    """A job name and how often it is enqueued."""

    job_name: str
    interval: timedelta

    def __post_init__(self) -> None:
        """Reject a non-positive interval."""
        if self.interval.total_seconds() <= 0:
            raise ValueError(f"interval of {self.job_name} must be positive")


def tick_dedupe_key(job_name: str, bucket: int) -> str:
    """Dedupe key of one job in one time bucket."""
    return f"{job_name}:tick:{bucket}"


class Scheduler:
    """Enqueue each periodic job once per interval, driven by the ``Clock``."""

    def __init__(
        self,
        *,
        queue: TaskQueue,
        clock: Clock,
        jobs: list[PeriodicJob],
        poll_interval_s: float = 1.0,
    ) -> None:
        """Wire the queue, clock and the periodic jobs."""
        self._queue = queue
        self._clock = clock
        self._jobs = tuple(jobs)
        self._poll = poll_interval_s
        self._last_bucket: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[str]:
        """Enqueue every job whose bucket has not been enqueued yet; return their names."""
        now = self._clock.now().timestamp()
        fired: list[str] = []
        for job in self._jobs:
            bucket = int(now // job.interval.total_seconds())
            if self._last_bucket.get(job.job_name) == bucket:
                continue
            try:
                self._queue.enqueue(
                    job.job_name, {}, dedupe_key=tick_dedupe_key(job.job_name, bucket)
                )
            except Exception:  # retried on the next poll
                _LOG.warning("could not enqueue periodic job %s", job.job_name, exc_info=True)
                continue
            self._last_bucket[job.job_name] = bucket
            fired.append(job.job_name)
        return fired

    def start(self) -> None:
        """Start the polling thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lab-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stop the polling thread and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                _LOG.warning("scheduler tick failed", exc_info=True)
            self._stop.wait(self._poll)
