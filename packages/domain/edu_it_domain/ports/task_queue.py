"""The ``TaskQueue`` port (Req 5.1).

Background work is handed off through this port. Production uses a Redis-backed
implementation (per ADR 0003); development/test uses an in-memory fake. The
surface is ``enqueue(job_name, payload, dedupe_key)`` /
``consume(job_name, handler)`` per the design, keeping producers (the API) and
consumers (the worker) decoupled behind a framework-free contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

JobPayload = Mapping[str, object]
"""A single job's payload: a JSON-serializable mapping of string keys."""

JobHandler = Callable[[JobPayload], None]
"""A consumer callback invoked once per delivered job with its payload."""


@runtime_checkable
class TaskQueue(Protocol):
    """Port for enqueuing and consuming background jobs."""

    def enqueue(
        self,
        job_name: str,
        payload: JobPayload,
        *,
        dedupe_key: str | None = None,
    ) -> None:
        """Enqueue a ``job_name`` job carrying ``payload``.

        When ``dedupe_key`` is provided, enqueuing another job with the same
        ``job_name`` and ``dedupe_key`` while an equivalent job is still pending
        does not add a duplicate, so at-least-once producers stay idempotent.
        """
        ...

    def consume(self, job_name: str, handler: JobHandler) -> None:
        """Register ``handler`` to receive jobs enqueued under ``job_name``.

        The concrete implementation decides the delivery loop; the domain only
        expresses which handler consumes which job name.
        """
        ...
