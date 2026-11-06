"""Unit tests for request id propagation through the job envelope.

kubernetes-deployment task 9.4.

**Validates: Requirements 13.8, 13.9, 13.10**
"""

from __future__ import annotations

import uuid

import pytest
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_infrastructure.telemetry import (
    REQUEST_ID_ENVELOPE_KEY,
    RequestIdTaskQueue,
    bind_request_id,
    bind_request_id_from_envelope,
    current_request_id,
    generate_request_id,
    reset_request_id,
    with_request_id,
)


def _queue() -> tuple[RequestIdTaskQueue, In_Memory_TaskQueue]:
    inner = In_Memory_TaskQueue()
    return RequestIdTaskQueue(inner), inner


def test_generated_request_id_is_a_uuid4() -> None:
    parsed = uuid.UUID(generate_request_id())
    assert parsed.version == 4


def test_enqueue_writes_the_bound_request_id_into_the_envelope() -> None:
    queue, inner = _queue()
    token = bind_request_id("req-1")
    try:
        queue.enqueue("lab.provision", {"session_id": "s-1"})
    finally:
        reset_request_id(token)
    (job,) = inner.pending_for("lab.provision")
    assert job.payload == {"session_id": "s-1", REQUEST_ID_ENVELOPE_KEY: "req-1"}


def test_enqueue_outside_a_request_adds_no_envelope_key() -> None:
    queue, inner = _queue()
    queue.enqueue("lab.reap", {})
    (job,) = inner.pending_for("lab.reap")
    assert job.payload == {}


def test_enqueue_does_not_mutate_the_callers_payload() -> None:
    queue, _ = _queue()
    payload = {"session_id": "s-1"}
    token = bind_request_id("req-1")
    try:
        queue.enqueue("lab.provision", payload)
    finally:
        reset_request_id(token)
    assert payload == {"session_id": "s-1"}


def test_worker_binds_the_envelope_id_before_the_handler_and_restores_after() -> None:
    queue, inner = _queue()
    seen: list[tuple[str | None, dict[str, object]]] = []
    queue.consume("lab.provision", lambda p: seen.append((current_request_id(), dict(p))))

    token = bind_request_id("req-9")
    try:
        queue.enqueue("lab.provision", {"session_id": "s-1"})
    finally:
        reset_request_id(token)

    assert current_request_id() is None
    inner.run_pending()
    assert seen == [("req-9", {"session_id": "s-1"})]
    assert current_request_id() is None


def test_worker_restores_the_context_when_the_handler_raises() -> None:
    queue, inner = _queue()

    def boom(_payload: object) -> None:
        raise RuntimeError("handler failed")

    queue.consume("lab.grade", boom)
    token = bind_request_id("req-2")
    try:
        queue.enqueue("lab.grade", {"run_id": "r-1"})
    finally:
        reset_request_id(token)
    with pytest.raises(RuntimeError):
        inner.run_pending()
    assert current_request_id() is None


def test_job_without_an_envelope_id_binds_nothing() -> None:
    queue, inner = _queue()
    seen: list[str | None] = []
    queue.consume("lab.reap", lambda _p: seen.append(current_request_id()))
    queue.enqueue("lab.reap", {})
    inner.run_pending()
    assert seen == [None]


def test_follow_on_jobs_from_a_handler_keep_the_same_request_id() -> None:
    queue, inner = _queue()
    queue.consume("first", lambda _p: queue.enqueue("second", {"n": 1}))
    token = bind_request_id("req-chain")
    try:
        queue.enqueue("first", {})
    finally:
        reset_request_id(token)
    inner.run_pending()
    (follow_on,) = inner.pending_for("second")
    assert follow_on.payload[REQUEST_ID_ENVELOPE_KEY] == "req-chain"


def test_dedupe_still_collapses_requests_with_different_ids() -> None:
    queue, inner = _queue()
    for request_id in ("req-a", "req-b"):
        token = bind_request_id(request_id)
        try:
            queue.enqueue("lab.provision", {"session_id": "s-1"}, dedupe_key="s-1")
        finally:
            reset_request_id(token)
    assert len(inner.pending_for("lab.provision")) == 1


def test_queue_extras_are_delegated_to_the_inner_queue() -> None:
    queue, inner = _queue()
    queue.enqueue("x", {})
    assert queue.pending == inner.pending


def test_helpers_round_trip_and_ignore_blank_ids() -> None:
    assert with_request_id({"a": 1}, None) == {"a": 1}
    assert with_request_id({"a": 1}, "") == {"a": 1}
    enveloped = with_request_id({"a": 1}, "r")
    clean, token = bind_request_id_from_envelope(enveloped)
    try:
        assert clean == {"a": 1}
        assert current_request_id() == "r"
    finally:
        assert token is not None
        reset_request_id(token)
    blank_clean, blank_token = bind_request_id_from_envelope({REQUEST_ID_ENVELOPE_KEY: "  "})
    assert blank_clean == {}
    assert blank_token is None
