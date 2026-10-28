"""Unit tests for the Redis-backed Redis_TaskQueue adapter (task 7.2, Req 5.1, 5.2, ADR 0003).

These exercise the adapter against a Redis test double (``fakeredis``) so no live
Redis server is required. Coverage mirrors the in-memory fake's contract plus the
Redis-specific concerns ADR 0003 calls out: FIFO ``enqueue`` / ``consume``, the
``dedupe_key`` collapse while a job is pending, and at-least-once delivery with
recovery of a job left in-flight by a crashed consumer.
"""

from __future__ import annotations

import fakeredis
import pytest
from edu_it_domain.ports import TaskQueue
from edu_it_domain.ports.task_queue import JobPayload
from edu_it_infrastructure.adapters import Redis_TaskQueue


@pytest.fixture
def client() -> fakeredis.FakeRedis:
    # decode_responses=True matches redis_from_url, so payloads come back as str.
    return fakeredis.FakeRedis(decode_responses=True)


def _collect_once(queue: Redis_TaskQueue, job_name: str, sink: list[JobPayload]) -> None:
    """Register a handler that records one payload then stops the consume loop."""

    def handler(payload: JobPayload) -> None:
        sink.append(payload)
        queue.stop()

    queue.consume(job_name, handler)


def test_adapter_satisfies_the_domain_port(client: fakeredis.FakeRedis) -> None:
    # Substitutable for the in-memory fake without changing domain code (Req 5.2).
    assert isinstance(Redis_TaskQueue(client), TaskQueue)


def test_enqueue_then_consume_delivers_the_payload(client: fakeredis.FakeRedis) -> None:
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("send_email", {"to": "x@example.com"})
    received: list[JobPayload] = []
    _collect_once(queue, "send_email", received)
    assert received == [{"to": "x@example.com"}]


def test_jobs_are_delivered_in_fifo_order(client: fakeredis.FakeRedis) -> None:
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("job", {"n": 1})
    queue.enqueue("job", {"n": 2})
    queue.enqueue("job", {"n": 3})
    received: list[JobPayload] = []

    def handler(payload: JobPayload) -> None:
        received.append(payload)
        if len(received) == 3:
            queue.stop()

    queue.consume("job", handler)
    assert received == [{"n": 1}, {"n": 2}, {"n": 3}]


def test_dedupe_key_collapses_duplicate_pending_enqueues(client: fakeredis.FakeRedis) -> None:
    # ADR 0003: same (job_name, dedupe_key) while pending does not duplicate.
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("gen", {"v": 1}, dedupe_key="k")
    queue.enqueue("gen", {"v": 2}, dedupe_key="k")
    assert client.llen("edu_it:taskqueue:queue:gen") == 1
    received: list[JobPayload] = []
    _collect_once(queue, "gen", received)
    # The first enqueue wins; the duplicate was dropped.
    assert received == [{"v": 1}]


def test_distinct_dedupe_keys_do_not_collapse(client: fakeredis.FakeRedis) -> None:
    queue = Redis_TaskQueue(client)
    queue.enqueue("gen", {"v": 1}, dedupe_key="a")
    queue.enqueue("gen", {"v": 2}, dedupe_key="b")
    assert client.llen("edu_it:taskqueue:queue:gen") == 2


def test_enqueue_without_dedupe_key_never_collapses(client: fakeredis.FakeRedis) -> None:
    queue = Redis_TaskQueue(client)
    queue.enqueue("gen", {"v": 1})
    queue.enqueue("gen", {"v": 1})
    assert client.llen("edu_it:taskqueue:queue:gen") == 2


def test_same_dedupe_key_allowed_again_after_the_job_is_consumed(
    client: fakeredis.FakeRedis,
) -> None:
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("gen", {"v": 1}, dedupe_key="k")
    received: list[JobPayload] = []
    _collect_once(queue, "gen", received)
    # The pending job drained and its dedupe marker cleared, so the key is free.
    queue.enqueue("gen", {"v": 2}, dedupe_key="k")
    assert client.llen("edu_it:taskqueue:queue:gen") == 1


def test_enqueued_payload_is_isolated_from_the_caller(client: fakeredis.FakeRedis) -> None:
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    payload = {"n": 1}
    queue.enqueue("job", payload)
    payload["n"] = 99
    received: list[JobPayload] = []
    _collect_once(queue, "job", received)
    assert received == [{"n": 1}]


def test_successful_handler_acknowledges_and_clears_the_processing_list(
    client: fakeredis.FakeRedis,
) -> None:
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("job", {"n": 1})
    received: list[JobPayload] = []
    _collect_once(queue, "job", received)
    # After a successful handler the job is gone from both the queue and the
    # processing list (at-least-once ack).
    assert client.llen("edu_it:taskqueue:queue:job") == 0
    assert client.llen("edu_it:taskqueue:processing:job:default") == 0


def test_handler_failure_leaves_the_job_in_flight_for_redelivery(
    client: fakeredis.FakeRedis,
) -> None:
    # At-least-once: a crashed handler leaves the job on the processing list.
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    queue.enqueue("job", {"n": 1})

    def failing(_payload: JobPayload) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        queue.consume("job", failing)
    assert client.llen("edu_it:taskqueue:processing:job:default") == 1

    # A fresh consumer recovers the in-flight job on startup (redelivery).
    recovered: list[JobPayload] = []
    recovery_queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    _collect_once(recovery_queue, "job", recovered)
    assert recovered == [{"n": 1}]
    assert client.llen("edu_it:taskqueue:processing:job:default") == 0
