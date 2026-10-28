"""Shared Redis connection support for the infrastructure adapters (ADR 0003/0004).

Redis is a single shared dependency backing both the task queue (ADR 0003) and
the server-side session / ephemeral state and sign-in rate-limit counters
(ADR 0004). Task 7.2 deliberately kept a tiny adapter-local ``redis_from_url``
inside ``task_queue.py`` so this task could introduce one shared helper once a
second Redis-backed adapter (the session and rate-limit stores) exists.

:func:`redis_from_url` is that shared helper. It builds a text-mode client
(``decode_responses=True``) from a connection URL, matching the JSON/string
encoding every Redis adapter in this package uses. ``task_queue.py`` now re-uses
it, so there is one place that constructs a Redis client; its behavior is
identical to the helper it replaced (same ``decode_responses=True`` text-mode
client), so the existing task-queue adapter and its tests are unaffected.
"""

from __future__ import annotations

import redis

__all__ = ["redis_from_url"]


def redis_from_url(url: str) -> redis.Redis:
    """Build a text-mode Redis client from a connection URL.

    ``decode_responses=True`` returns ``str`` replies, matching the JSON/string
    encoding the task-queue, session, and rate-limit adapters use. This is the
    single shared constructor for Redis clients in the infrastructure layer
    (ADR 0003 queue backing and ADR 0004 session / rate-limit state share the
    same Redis server).
    """
    return redis.Redis.from_url(url, decode_responses=True)
