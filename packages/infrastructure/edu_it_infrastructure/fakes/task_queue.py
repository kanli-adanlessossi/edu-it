"""The in-memory ``TaskQueue`` fake (Req 5.2, ADR 0003).

:class:`In_Memory_TaskQueue` implements the domain
:class:`~edu_it_domain.ports.task_queue.TaskQueue` port with in-process queues,
so producers (the API) and consumers (the worker) can be exercised in tests and
local development without Redis.

The surface follows ADR 0003: ``enqueue(job_name, payload, dedupe_key)`` and
``consume(job_name, handler)``. A ``dedupe_key`` collapses duplicate enqueues
while an equivalent job is still pending, matching the Redis-backed
implementation's deduplication so at-least-once producers stay idempotent. The
delivery loop here is synchronous and explicit: :meth:`consume` registers a
handler, and :meth:`run_pending` drains the queue by invoking the handlers. This
keeps tests deterministic (no background thread) while remaining substitutable
for the production adapter, which supplies its own delivery loop behind the same
port.
"""

from __future__ import annotations

from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue

__all__ = ["In_Memory_TaskQueue", "QueuedJob"]


class QueuedJob:
    """A single enqueued job: its ``job_name``, ``payload``, and ``dedupe_key``."""

    __slots__ = ("dedupe_key", "job_name", "payload")

    def __init__(
        self,
        job_name: str,
        payload: JobPayload,
        dedupe_key: str | None,
    ) -> None:
        self.job_name = job_name
        self.payload = payload
        self.dedupe_key = dedupe_key


class In_Memory_TaskQueue(TaskQueue):
    """A :class:`TaskQueue` backed by in-process queues.

    Satisfies the :class:`~edu_it_domain.ports.task_queue.TaskQueue` protocol, so
    the API enqueues and the worker consumes exactly as they do in production.
    """

    __test__ = False

    def __init__(self) -> None:
        """Create an empty queue with no registered handlers."""
        self._pending: list[QueuedJob] = []
        self._handlers: dict[str, JobHandler] = {}
        # Dedupe keys of jobs still pending, per job name, so a repeated enqueue
        # with the same (job_name, dedupe_key) collapses to a single job while
        # the first is still pending (ADR 0003).
        self._pending_dedupe: set[tuple[str, str]] = set()

    def enqueue(
        self,
        job_name: str,
        payload: JobPayload,
        *,
        dedupe_key: str | None = None,
    ) -> None:
        """Enqueue a ``job_name`` job carrying ``payload`` (ADR 0003).

        When ``dedupe_key`` is given, enqueuing another job with the same
        ``job_name`` and ``dedupe_key`` while an equivalent job is still pending
        does not add a duplicate. The payload is copied so a later mutation of
        the caller's mapping cannot change what was enqueued.
        """
        if dedupe_key is not None:
            marker = (job_name, dedupe_key)
            if marker in self._pending_dedupe:
                return
            self._pending_dedupe.add(marker)
        self._pending.append(QueuedJob(job_name, dict(payload), dedupe_key))

    def consume(self, job_name: str, handler: JobHandler) -> None:
        """Register ``handler`` to receive jobs enqueued under ``job_name``.

        Registering a second handler for the same ``job_name`` replaces the
        first, so there is exactly one consumer per job name.
        """
        self._handlers[job_name] = handler

    def run_pending(self) -> int:
        """Deliver every pending job for which a handler is registered.

        Returns the number of jobs delivered. Jobs with no registered handler
        stay pending, matching a queue whose consumer has not yet started. A
        handler that raises propagates the exception after the job has been
        removed from the pending set, so a failed delivery does not silently
        replay here.
        """
        delivered = 0
        remaining: list[QueuedJob] = []
        for job in self._pending:
            handler = self._handlers.get(job.job_name)
            if handler is None:
                remaining.append(job)
                continue
            if job.dedupe_key is not None:
                self._pending_dedupe.discard((job.job_name, job.dedupe_key))
            handler(job.payload)
            delivered += 1
        self._pending = remaining
        return delivered

    @property
    def pending(self) -> tuple[QueuedJob, ...]:
        """The jobs not yet delivered, in enqueue order (for test inspection)."""
        return tuple(self._pending)

    def pending_for(self, job_name: str) -> tuple[QueuedJob, ...]:
        """The pending jobs enqueued under ``job_name``, in enqueue order."""
        return tuple(job for job in self._pending if job.job_name == job_name)
