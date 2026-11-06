"""Request id propagation through the job envelope (kubernetes-deployment Req 13.8-13.10).

The Api binds a request id for every request (``X-Request-ID`` or a generated
UUID4). :class:`RequestIdTaskQueue` decorates a
:class:`~edu_it_domain.ports.task_queue.TaskQueue` so that:

* ``enqueue`` writes the request id bound to the current context into the job
  envelope under :data:`REQUEST_ID_ENVELOPE_KEY` (Req 13.10);
* ``consume`` wraps each handler so the Worker reads the id from the envelope and
  binds it to the log context before the handler runs, then restores the previous
  context, and the handler sees the original payload without the envelope key
  (Req 13.8, 13.10).

A job enqueued outside a request (a scheduler tick) carries no id, and the Worker
binds nothing for it. A job enqueued by a handler that runs with a bound id
carries the same id on, so follow-on jobs stay correlated.

The decorator is framework-free and works over any ``TaskQueue`` (the Redis
adapter and the in-memory fake), so dedupe behavior is unchanged: the key is not
part of the dedupe identity.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Final

from edu_it_infrastructure.telemetry.logging import (
    bind_request_id,
    current_request_id,
    reset_request_id,
)

if TYPE_CHECKING:
    from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue

__all__ = [
    "REQUEST_ID_ENVELOPE_KEY",
    "RequestIdTaskQueue",
    "bind_request_id_from_envelope",
    "generate_request_id",
    "with_request_id",
]

#: Reserved envelope key that carries the request id. Business payload keys never
#: use a leading double underscore.
REQUEST_ID_ENVELOPE_KEY: Final[str] = "__request_id__"


def generate_request_id() -> str:
    """Return a new request id as a UUID4 string."""
    return str(uuid.uuid4())


def with_request_id(payload: JobPayload, request_id: str | None) -> dict[str, object]:
    """Return a copy of ``payload`` carrying ``request_id`` in the envelope key.

    When ``request_id`` is ``None`` or empty the copy has no envelope key.
    """
    enveloped = {k: v for k, v in payload.items() if k != REQUEST_ID_ENVELOPE_KEY}
    if request_id:
        enveloped[REQUEST_ID_ENVELOPE_KEY] = request_id
    return enveloped


def bind_request_id_from_envelope(
    payload: JobPayload,
) -> tuple[dict[str, object], Any]:
    """Read the request id from ``payload`` and bind it to the log context.

    Returns ``(clean_payload, token)``: the payload without the envelope key, and
    the token to pass to :func:`reset_request_id` (``None`` when nothing was
    bound because the envelope carried no usable id).
    """
    clean = {k: v for k, v in payload.items() if k != REQUEST_ID_ENVELOPE_KEY}
    raw = payload.get(REQUEST_ID_ENVELOPE_KEY)
    if isinstance(raw, str) and raw.strip():
        return clean, bind_request_id(raw.strip())
    return clean, None


class RequestIdTaskQueue:
    """A ``TaskQueue`` decorator that propagates the request id (Req 13.10)."""

    def __init__(self, inner: TaskQueue) -> None:
        """Wrap ``inner``; every other attribute is delegated to it."""
        self._inner = inner

    def enqueue(
        self,
        job_name: str,
        payload: JobPayload,
        *,
        dedupe_key: str | None = None,
    ) -> None:
        """Enqueue with the current request id written into the envelope."""
        self._inner.enqueue(
            job_name,
            with_request_id(payload, current_request_id()),
            dedupe_key=dedupe_key,
        )

    def consume(self, job_name: str, handler: JobHandler) -> None:
        """Consume with the request id bound around each handler call."""

        def bound(payload: JobPayload) -> None:
            clean, token = bind_request_id_from_envelope(payload)
            try:
                handler(clean)
            finally:
                if token is not None:
                    reset_request_id(token)

        self._inner.consume(job_name, bound)

    def __getattr__(self, name: str) -> Any:
        """Delegate queue extras (``stop``, ``run_pending``, ``pending``) to the inner queue."""
        return getattr(self._inner, name)
