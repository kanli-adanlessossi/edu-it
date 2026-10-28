"""Production port adapters (Req 5.1).

Concrete implementations of the framework-free domain ports
(``edu_it_domain.ports``) against real external systems. Each adapter is
substitutable for its in-memory / console fake without changing domain code
(Req 5.2); the fakes live under ``edu_it_infrastructure.fakes``.

This phase's adapters:

- :class:`~edu_it_infrastructure.adapters.object_storage.FileSystem_ObjectStorage`
  — the production :class:`~edu_it_domain.ports.object_storage.ObjectStorage`
  (GDPR export / lab artifacts, Req 14).
- :class:`~edu_it_infrastructure.adapters.task_queue.Redis_TaskQueue` — the
  Redis-backed :class:`~edu_it_domain.ports.task_queue.TaskQueue` (ADR 0003).
- :class:`~edu_it_infrastructure.adapters.session_store.Redis_SessionStore` — the
  Redis-backed server-side :class:`~edu_it_domain.ports.session_store.SessionStore`
  (ADR 0004).
- :class:`~edu_it_infrastructure.adapters.rate_limiter.Redis_SignInRateLimiter` —
  the Redis-backed :class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter`
  (Req 12).

The PostgreSQL identity repositories live alongside the schema under
:mod:`edu_it_infrastructure.persistence.repositories`. All Redis adapters build
their client through the shared
:func:`~edu_it_infrastructure.adapters.redis_support.redis_from_url` helper.
"""

from edu_it_infrastructure.adapters.object_storage import FileSystem_ObjectStorage
from edu_it_infrastructure.adapters.rate_limiter import Redis_SignInRateLimiter
from edu_it_infrastructure.adapters.redis_support import redis_from_url
from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
from edu_it_infrastructure.adapters.task_queue import Redis_TaskQueue

__all__ = [
    "FileSystem_ObjectStorage",
    "Redis_SessionStore",
    "Redis_SignInRateLimiter",
    "Redis_TaskQueue",
    "redis_from_url",
]
