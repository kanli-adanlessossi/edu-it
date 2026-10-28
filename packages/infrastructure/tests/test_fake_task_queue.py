"""Unit tests for the in-memory In_Memory_TaskQueue fake (task 5.3, Req 5.2, ADR 0003).

Example and edge-case coverage: the fake satisfies the domain ``TaskQueue`` port,
``enqueue`` / ``consume`` behave per ADR 0003 (including ``dedupe_key`` collapse
while a job is pending), and ``run_pending`` drains the queue to the registered
handlers. The formal substitutability assertions are task 5.4.
"""

from __future__ import annotations

from edu_it_domain.ports import TaskQueue
from edu_it_domain.ports.task_queue import JobPayload
from edu_it_infrastructure.fakes import In_Memory_TaskQueue


def test_task_queue_satisfies_the_domain_port() -> None:
    # Substitutable for the Redis-backed adapter without changing domain code (Req 5.2).
    assert isinstance(In_Memory_TaskQueue(), TaskQueue)


def test_enqueue_then_consume_delivers_the_payload() -> None:
    queue = In_Memory_TaskQueue()
    received: list[JobPayload] = []
    queue.consume("send_email", received.append)
    queue.enqueue("send_email", {"to": "x@example.com"})
    delivered = queue.run_pending()
    assert delivered == 1
    assert received == [{"to": "x@example.com"}]


def test_consume_can_be_registered_before_or_after_enqueue() -> None:
    # A job enqueued before its consumer registers is still delivered on drain.
    queue = In_Memory_TaskQueue()
    queue.enqueue("job", {"n": 1})
    received: list[JobPayload] = []
    queue.consume("job", received.append)
    queue.run_pending()
    assert received == [{"n": 1}]


def test_dedupe_key_collapses_duplicate_pending_enqueues() -> None:
    # ADR 0003: same (job_name, dedupe_key) while pending does not duplicate.
    queue = In_Memory_TaskQueue()
    queue.enqueue("gen", {"v": 1}, dedupe_key="k")
    queue.enqueue("gen", {"v": 2}, dedupe_key="k")
    assert len(queue.pending_for("gen")) == 1
    # The first enqueue wins; the duplicate is dropped.
    assert queue.pending_for("gen")[0].payload == {"v": 1}


def test_same_dedupe_key_allowed_again_after_the_job_drains() -> None:
    queue = In_Memory_TaskQueue()
    queue.consume("gen", lambda _payload: None)
    queue.enqueue("gen", {"v": 1}, dedupe_key="k")
    queue.run_pending()
    # The pending job drained, so the dedupe key is free again.
    queue.enqueue("gen", {"v": 2}, dedupe_key="k")
    assert len(queue.pending_for("gen")) == 1


def test_distinct_dedupe_keys_do_not_collapse() -> None:
    queue = In_Memory_TaskQueue()
    queue.enqueue("gen", {"v": 1}, dedupe_key="a")
    queue.enqueue("gen", {"v": 2}, dedupe_key="b")
    assert len(queue.pending_for("gen")) == 2


def test_enqueue_without_dedupe_key_never_collapses() -> None:
    queue = In_Memory_TaskQueue()
    queue.enqueue("gen", {"v": 1})
    queue.enqueue("gen", {"v": 1})
    assert len(queue.pending_for("gen")) == 2


def test_run_pending_leaves_jobs_with_no_handler_pending() -> None:
    queue = In_Memory_TaskQueue()
    queue.enqueue("unhandled", {"v": 1})
    assert queue.run_pending() == 0
    assert len(queue.pending_for("unhandled")) == 1


def test_consume_replaces_a_prior_handler_for_the_same_job_name() -> None:
    queue = In_Memory_TaskQueue()
    first: list[JobPayload] = []
    second: list[JobPayload] = []
    queue.consume("job", first.append)
    queue.consume("job", second.append)
    queue.enqueue("job", {"n": 1})
    queue.run_pending()
    assert first == []
    assert second == [{"n": 1}]


def test_enqueued_payload_is_isolated_from_the_caller() -> None:
    queue = In_Memory_TaskQueue()
    payload = {"n": 1}
    queue.enqueue("job", payload)
    payload["n"] = 99
    assert queue.pending_for("job")[0].payload == {"n": 1}
