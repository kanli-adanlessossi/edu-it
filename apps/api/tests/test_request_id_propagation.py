"""Request id flows from the HTTP middleware into the job envelope and the Worker.

kubernetes-deployment task 9.4. The middleware resolves ``X-Request-ID`` (or
generates a UUID4), and a job enqueued while the request is handled carries it.
The middleware is driven server-less (a Starlette ``Request`` and a ``call_next``
that enqueues), because the FastAPI ``TestClient`` (httpx) is not a dependency of
this layer.

**Validates: Requirements 13.8, 13.9, 13.10**
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from edu_it_api.middleware import RequestLoggingMiddleware
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_infrastructure.telemetry import (
    REQUEST_ID_ENVELOPE_KEY,
    RequestIdTaskQueue,
    current_request_id,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/lab-sessions",
            "raw_path": b"/v1/lab-sessions",
            "query_string": b"",
            "headers": headers,
        }
    )


async def _unused_asgi_app(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("the wrapped ASGI app is bypassed by the sentinel call_next")


def _handle(
    headers: list[tuple[bytes, bytes]],
) -> tuple[Response, In_Memory_TaskQueue, str | None]:
    """Dispatch one request whose route enqueues a job; return the response and queue."""
    inner = In_Memory_TaskQueue()
    queue = RequestIdTaskQueue(inner)
    bound_in_route: list[str | None] = []

    async def route(_request: Request) -> Response:
        bound_in_route.append(current_request_id())
        queue.enqueue("lab.provision", {"session_id": "s-1"})
        return JSONResponse({"ok": True})

    middleware = RequestLoggingMiddleware(_unused_asgi_app)
    response = asyncio.run(middleware.dispatch(_request(headers), route))
    return response, inner, bound_in_route[0]


def test_inbound_request_id_reaches_the_job_envelope_and_the_worker() -> None:
    response, inner, bound = _handle([(b"x-request-id", b"req-abc")])

    assert bound == "req-abc"
    assert response.headers["x-request-id"] == "req-abc"
    (job,) = inner.pending_for("lab.provision")
    assert job.payload[REQUEST_ID_ENVELOPE_KEY] == "req-abc"

    # The Worker side: a fresh context binds the id from the envelope.
    seen: list[str | None] = []
    RequestIdTaskQueue(inner).consume("lab.provision", lambda _p: seen.append(current_request_id()))
    assert current_request_id() is None
    inner.run_pending()
    assert seen == ["req-abc"]


def test_missing_request_id_is_generated_as_uuid4_and_propagated() -> None:
    response, inner, bound = _handle([])

    generated = response.headers["x-request-id"]
    assert uuid.UUID(generated).version == 4
    assert bound == generated
    (job,) = inner.pending_for("lab.provision")
    assert job.payload[REQUEST_ID_ENVELOPE_KEY] == generated


def test_context_is_cleared_after_the_request() -> None:
    _handle([(b"x-request-id", b"req-abc")])
    assert current_request_id() is None
