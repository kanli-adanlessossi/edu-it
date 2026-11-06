"""Property 18: Worker shutdown requeues unfinished jobs (kubernetes-deployment task 8.9).

For any set of in-flight jobs with their remaining durations and any grace period, jobs that
finish before ``grace - 5 s`` are acknowledged, every other job is requeued with its attempt
count unchanged, and no job is lost or left running.

The ``ShutdownCoordinator`` runs on a fake monotonic clock and sleep (no real waiting) over a
``Redis_TaskQueue`` backed by ``fakeredis``: finishing a job acknowledges it exactly as the
adapter does (removes it from the processing list), and the coordinator's re-queue calls the
real ``Redis_TaskQueue.requeue_in_flight``.

**Validates: Requirements 14.10, 14.11**
"""

from __future__ import annotations

import fakeredis
from edu_it_domain.config import WORKER_SHUTDOWN_MARGIN_SECONDS
from edu_it_infrastructure.adapters import Redis_TaskQueue
from edu_it_worker.lifecycle import ShutdownCoordinator
from hypothesis import given, settings
from hypothesis import strategies as st

JOB = "job"
START = 1000.0

_job = st.tuples(
    st.integers(min_value=1, max_value=200),  # remaining duration, whole seconds
    st.integers(min_value=0, max_value=5),  # attempt count carried in the payload
    st.booleans(),  # whether the job carries a dedupe key
)


class _Clock:
    def __init__(self) -> None:
        self.t = START

    def __call__(self) -> float:
        return self.t


class _Runtime:
    """Worker runtime over a real Redis_TaskQueue: jobs ack themselves when their time is up."""

    def __init__(self, queue: Redis_TaskQueue, clock: _Clock, jobs: list[tuple[str, float]]):
        self.queue = queue
        self.clock = clock
        self.pending = list(jobs)  # (encoded, finish time)
        self.processing = queue._processing_key(JOB)
        self.client = queue._redis
        self.stopped = False
        self.acked: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.clock.t += seconds
        for encoded, finish_at in list(self.pending):
            if finish_at <= self.clock.t:
                self.client.lrem(self.processing, 1, encoded)  # the adapter's acknowledge
                self.pending.remove((encoded, finish_at))
                self.acked.append(encoded)

    def stop_consuming(self) -> None:
        self.stopped = True

    def in_flight(self) -> bool:
        return bool(self.pending)

    def requeue_in_flight(self) -> int:
        self.pending.clear()  # handlers are abandoned once their jobs are back on the queue
        return self.queue.requeue_in_flight()


@settings(max_examples=100, deadline=None)
@given(jobs=st.lists(_job, min_size=1, max_size=12), grace=st.integers(min_value=0, max_value=150))
def test_shutdown_acks_finished_jobs_and_requeues_the_rest_unchanged(
    jobs: list[tuple[int, int, bool]], grace: int
) -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    queue = Redis_TaskQueue(client, block_timeout_seconds=0.05)
    # Register the job name as consumed (as a running consumer would) via a drained sentinel.
    queue.enqueue(JOB, {"sentinel": True})
    queue.consume(JOB, lambda _p: queue.stop())

    encoded_jobs: list[tuple[str, float, int]] = []
    for index, (duration, attempts, deduped) in enumerate(jobs):
        encoded = Redis_TaskQueue._encode(
            JOB, {"id": index, "attempts": attempts}, f"k{index}" if deduped else None
        )
        client.rpush(queue._processing_key(JOB), encoded)
        encoded_jobs.append((encoded, START + duration, duration))

    clock = _Clock()
    runtime = _Runtime(queue, clock, [(e, f) for e, f, _d in encoded_jobs])
    coordinator = ShutdownCoordinator(
        runtime,
        grace_s=grace,
        monotonic=clock,
        sleep=runtime.sleep,
        poll_s=1.0,
    )

    requeued = coordinator.drain()

    budget = max(grace - WORKER_SHUTDOWN_MARGIN_SECONDS, 0)
    finished = [e for e, _f, d in encoded_jobs if d <= budget]
    unfinished = [e for e, _f, d in encoded_jobs if d > budget]

    assert runtime.stopped
    assert sorted(runtime.acked) == sorted(finished)  # finished before grace - 5 s: acknowledged
    assert requeued == len(unfinished)
    # Nothing left running, and no job lost: unfinished jobs are back on the queue in order,
    # byte-for-byte identical, so the attempt count is unchanged.
    assert client.llen(queue._processing_key(JOB)) == 0
    assert not runtime.in_flight()
    assert client.lrange(queue._queue_key(JOB), 0, -1) == unfinished
    # The coordinator never waits past the deadline.
    assert clock.t <= START + max(budget, 0) + 1.0
