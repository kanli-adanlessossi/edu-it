"""The Redis-backed ``TaskQueue`` adapter (task 7.2, Req 5.1, 5.2, ADR 0003).

:class:`Redis_TaskQueue` implements the domain
:class:`~edu_it_domain.ports.task_queue.TaskQueue` port over Redis, the backing
pinned by ADR 0003. It keeps producers (the API) and consumers (the worker)
decoupled behind the framework-free ``enqueue(job_name, payload, dedupe_key)`` /
``consume(job_name, handler)`` surface, and is substitutable for the in-memory
fake without changing domain code (Req 5.2).

Design (ADR 0003):

- **Queue** — each ``job_name`` is a Redis list (``{prefix}:queue:{job_name}``).
  ``enqueue`` right-pushes a JSON-encoded job; ``consume`` left-pops in FIFO
  order, so jobs are delivered oldest-first.
- **At-least-once delivery** — ``consume`` moves a job from the queue onto a
  per-consumer *processing* list atomically (``BLPOP`` semantics via
  ``BRPOPLPUSH``/``BLMOVE``) before running the handler, and only removes it
  from the processing list once the handler returns. If the process crashes
  mid-handling the job remains on the processing list and is re-driven on the
  next start, so a job is delivered at least once. Handlers must therefore be
  idempotent.
- **dedupe_key collapse point** — when ``enqueue`` is given a ``dedupe_key`` it
  first tries to add ``{job_name}\x00{dedupe_key}`` to a Redis set
  (``{prefix}:dedupe:{job_name}``). If the marker is already present an
  equivalent job is still pending, so the enqueue is dropped and no duplicate is
  added. The marker is removed at the moment the job is handed to a handler
  (just before processing), so retried/duplicate submissions collapse to one
  pending job while an equivalent one waits, matching the fake.

The connection is created by the shared
:func:`~edu_it_infrastructure.adapters.redis_support.redis_from_url` helper,
re-exported here so existing ``from ...adapters.task_queue import redis_from_url``
imports keep working. Task 7.2 kept a tiny adapter-local copy of this helper; now
that the session / rate-limit stores (ADR 0004) also need a Redis client, that
copy has moved to ``redis_support`` as the single shared constructor and this
module re-uses it unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import redis
from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue

from edu_it_infrastructure.adapters.redis_support import redis_from_url

__all__ = ["Redis_TaskQueue", "redis_from_url"]

_DEDUPE_SEPARATOR = "\x00"
"""Separator joining job_name and dedupe_key in a dedupe-set member (NUL cannot
appear in a job name or dedupe key)."""


class Redis_TaskQueue(TaskQueue):
    """A :class:`TaskQueue` backed by Redis lists and sets (ADR 0003).

    Satisfies the :class:`~edu_it_domain.ports.task_queue.TaskQueue` protocol, so
    the API enqueues and the worker consumes exactly as they do against the
    in-memory fake.
    """

    def __init__(
        self,
        client: redis.Redis,
        *,
        namespace: str = "edu_it:taskqueue",
        consumer_name: str = "default",
        block_timeout_seconds: float = 1.0,
    ) -> None:
        """Create a queue over ``client``.

        ``namespace`` prefixes every Redis key so the queue's keys do not collide
        with other Redis uses (session / rate-limit state share the same server
        per ADR 0003/0004). ``consumer_name`` names this consumer's processing
        list so at-least-once recovery is per consumer. ``block_timeout_seconds``
        is how long :meth:`consume` blocks waiting for a job before re-checking
        its stop flag.
        """
        self._redis = client
        self._namespace = namespace
        self._consumer_name = consumer_name
        self._block_timeout = block_timeout_seconds
        self._stopped = False

    # -- Key helpers ----------------------------------------------------------

    def _queue_key(self, job_name: str) -> str:
        return f"{self._namespace}:queue:{job_name}"

    def _dedupe_key(self, job_name: str) -> str:
        return f"{self._namespace}:dedupe:{job_name}"

    def _processing_key(self, job_name: str) -> str:
        return f"{self._namespace}:processing:{job_name}:{self._consumer_name}"

    @staticmethod
    def _dedupe_member(job_name: str, dedupe_key: str) -> str:
        return f"{job_name}{_DEDUPE_SEPARATOR}{dedupe_key}"

    @staticmethod
    def _as_str(value: object) -> str:
        """Coerce a Redis reply to ``str``.

        The client is built with ``decode_responses=True`` so replies are
        already ``str`` at runtime; the client's static type still admits
        ``bytes``, so decode defensively to keep the JSON decode well-typed.
        """
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    # -- Producer -------------------------------------------------------------

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
        does not add a duplicate: the marker is added atomically and, if it was
        already present, the enqueue is dropped. The payload is JSON-encoded, so
        a later mutation of the caller's mapping cannot change what was enqueued.
        """
        if dedupe_key is not None:
            member = self._dedupe_member(job_name, dedupe_key)
            # SADD returns the number of members actually added (0 if present),
            # giving an atomic "claim this dedupe key" check.
            added = self._redis.sadd(self._dedupe_key(job_name), member)
            if not added:
                return
        encoded = self._encode(job_name, payload, dedupe_key)
        self._redis.rpush(self._queue_key(job_name), encoded)

    # -- Consumer -------------------------------------------------------------

    def consume(self, job_name: str, handler: JobHandler) -> None:
        """Deliver jobs enqueued under ``job_name`` to ``handler`` (ADR 0003).

        First re-drives any jobs left on this consumer's processing list by a
        previous crashed run (at-least-once recovery), then blocks for new jobs.
        Each job is moved atomically onto the processing list before the handler
        runs and removed only after the handler returns, so a crash mid-handling
        re-delivers the job. The dedupe marker (if any) is cleared at the moment
        the job is handed to the handler, freeing the key for a fresh enqueue.

        The loop runs until :meth:`stop` is called (the worker's shutdown path).
        A handler exception propagates to the caller with the job left on the
        processing list so it is retried, rather than being silently dropped.
        """
        queue_key = self._queue_key(job_name)
        processing_key = self._processing_key(job_name)
        self._stopped = False

        # Recover any job left in-flight by a previous crashed run.
        pending = self._redis.lindex(processing_key, -1)
        if pending is not None:
            self._dispatch(job_name, self._as_str(pending), handler, processing_key)

        while not self._stopped:
            # BLMOVE supports a fractional timeout at runtime; the client's type
            # stub narrows it to int, so cast to keep sub-second polling.
            encoded = self._redis.blmove(
                queue_key,
                processing_key,
                cast(int, self._block_timeout),
                "LEFT",
                "RIGHT",
            )
            if encoded is None:
                continue  # timed out with no job; re-check the stop flag
            self._dispatch(job_name, self._as_str(encoded), handler, processing_key)

    def stop(self) -> None:
        """Request the :meth:`consume` loop to exit after its current wait."""
        self._stopped = True

    def _dispatch(
        self,
        job_name: str,
        encoded: str,
        handler: JobHandler,
        processing_key: str,
    ) -> None:
        """Hand one encoded job to ``handler`` and acknowledge it on success.

        The dedupe marker is removed before the handler runs so a duplicate
        enqueue arriving during handling is accepted as a fresh job; the job is
        removed from the processing list only after the handler returns, giving
        at-least-once delivery.
        """
        payload, dedupe_key = self._decode(encoded)
        if dedupe_key is not None:
            self._redis.srem(
                self._dedupe_key(job_name),
                self._dedupe_member(job_name, dedupe_key),
            )
        handler(payload)
        # Acknowledge: drop exactly this job from the processing list.
        self._redis.lrem(processing_key, 1, encoded)

    # -- Encoding -------------------------------------------------------------

    @staticmethod
    def _encode(
        job_name: str,
        payload: JobPayload,
        dedupe_key: str | None,
    ) -> str:
        """JSON-encode a job for storage on the Redis list."""
        envelope = {
            "job_name": job_name,
            "payload": dict(payload),
            "dedupe_key": dedupe_key,
        }
        return json.dumps(envelope, sort_keys=True)

    @staticmethod
    def _decode(encoded: str) -> tuple[JobPayload, str | None]:
        """Decode a stored job back into its payload and dedupe key."""
        envelope = json.loads(encoded)
        payload = envelope.get("payload", {})
        if not isinstance(payload, Mapping):
            payload = {}
        dedupe_key = envelope.get("dedupe_key")
        if dedupe_key is not None and not isinstance(dedupe_key, str):
            dedupe_key = None
        return payload, dedupe_key
