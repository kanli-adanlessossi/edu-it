"""Property test: request id propagation.

Feature: kubernetes-deployment, Property 19: Request id propagation

*For any* inbound request with or without an ``X-Request-ID`` header, the request has
a non-empty id (the header value, or a new one when absent), the id appears in every
log record of the request and in every job envelope the request enqueues, and the
Worker log records for that job carry the same id.

kubernetes-deployment task 9.5. Drives the middleware server-less (a Starlette
``Request`` and a sentinel ``call_next``), as in ``test_request_id_propagation.py``
(task 9.4), but across a generated population of inbound header values.

**Validates: Requirements 13.8, 13.9, 13.10**
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from edu_it_api.logging import JsonLogFormatter
from edu_it_api.middleware import RequestLoggingMiddleware
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_infrastructure.telemetry import (
    REQUEST_ID_ENVELOPE_KEY,
    RequestIdTaskQueue,
    current_request_id,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Inbound header values: ``None`` means no header at all; otherwise an arbitrary
# string including blank, overlong and surrounding-whitespace values, all of which
# ``resolve_request_id`` must turn into *some* non-empty id (apps/api/edu_it_api/
# logging.py: blank or over-128-char values are replaced by a generated id).
_header_values = st.one_of(
    st.none(),
    st.text(
        # HTTP header values are bytes decoded as latin-1 by Starlette.
        alphabet=st.characters(max_codepoint=255, blacklist_characters="\x00\r\n"),
        max_size=200,
    ),
)


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
    header_value: str | None,
) -> tuple[Response, In_Memory_TaskQueue, list[str | None], list[str]]:
    """Dispatch one request whose route logs and enqueues a job.

    Returns the response, the inner queue, the request id observed inside the
    route, and the log lines formatted while the request was in flight (the id is
    read from the context at format time, so formatting must happen at emit time).
    """
    inner = In_Memory_TaskQueue()
    queue = RequestIdTaskQueue(inner)
    seen_in_route: list[str | None] = []
    records: list[str] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    route_logger = logging.getLogger("edu_it_api.test.route")
    route_logger.setLevel(logging.INFO)
    route_logger.propagate = False
    collector = _Collector()
    collector.setFormatter(JsonLogFormatter())
    route_logger.addHandler(collector)

    async def route(_request: Request) -> Response:
        seen_in_route.append(current_request_id())
        route_logger.info("handling")
        queue.enqueue("lab.provision", {"session_id": "s-1"})
        seen_in_route.append(current_request_id())
        return JSONResponse({"ok": True})

    try:
        middleware = RequestLoggingMiddleware(_unused_asgi_app)
        headers = (
            [] if header_value is None else [(b"x-request-id", header_value.encode("latin-1"))]
        )
        response = asyncio.run(middleware.dispatch(_request(headers), route))
    finally:
        route_logger.removeHandler(collector)

    return response, inner, seen_in_route, records


@given(header_value=_header_values)
@settings(max_examples=100, deadline=None)
def test_property_19_request_id_propagates_from_request_to_logs_and_job_envelope(
    header_value: str | None,
) -> None:
    response, inner, seen_in_route, records = _handle(header_value)

    # The request has a non-empty id, echoed on the response (Req 13.9).
    resolved = response.headers["x-request-id"]
    assert resolved
    trimmed = header_value.strip() if header_value is not None else ""
    if trimmed and len(trimmed) <= 128:
        assert resolved == trimmed
    else:
        # Blank, missing or overlong header values fall back to a generated UUID4.
        assert uuid.UUID(resolved).version == 4

    # The id is bound for every point in the request's call stack (Req 13.8).
    assert seen_in_route == [resolved, resolved]

    # Every log record emitted while the request is handled carries the id.
    assert records, "the route must have produced at least one log record"
    for line in records:
        assert json.loads(line)["request_id"] == resolved

    # The id appears in the job envelope the request enqueued (Req 13.10).
    (job,) = inner.pending_for("lab.provision")
    assert job.payload[REQUEST_ID_ENVELOPE_KEY] == resolved

    # The Worker binds the same id from the envelope before running the handler,
    # and the context is clear both before and after (Req 13.8, 13.10).
    assert current_request_id() is None
    worker_seen: list[str | None] = []
    worker_lines: list[str] = []
    worker_logger = logging.getLogger("edu_it_worker.test")
    worker_logger.setLevel(logging.INFO)
    worker_logger.propagate = False
    worker_handler = logging.Handler()
    worker_handler.setFormatter(JsonLogFormatter())
    worker_handler.emit = lambda record: worker_lines.append(worker_handler.format(record))  # type: ignore[method-assign]
    worker_logger.addHandler(worker_handler)

    def worker_job(_payload: object) -> None:
        worker_seen.append(current_request_id())
        worker_logger.info("job.run")

    try:
        RequestIdTaskQueue(inner).consume("lab.provision", worker_job)
        inner.run_pending()
    finally:
        worker_logger.removeHandler(worker_handler)

    assert worker_seen == [resolved]
    assert [json.loads(line)["request_id"] for line in worker_lines] == [resolved]
    assert current_request_id() is None
