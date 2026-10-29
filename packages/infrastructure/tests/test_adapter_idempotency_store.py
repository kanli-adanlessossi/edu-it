"""Tests for the Redis-backed ``IdempotencyStore`` (task 18.5).

Two store instances over one (fake) Redis server stand in for two worker
processes: the at-most-once decision must come from the shared store, not from a
per-instance lock.

**Validates: Requirements 18.1, 18.2, 23.1, 23.2, 23.3**
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import fakeredis
import pytest
from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    IdempotencyConflictError,
)
from edu_it_application.learning.attempt_codec import (
    decode_attempt_result,
    encode_attempt_result,
)
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.idempotency import ReservationStatus
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)


def _store(server: fakeredis.FakeServer) -> Redis_IdempotencyStore:
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    return Redis_IdempotencyStore(client, encode=lambda r: str(r), decode=lambda s: s)


def test_reserve_complete_replay_conflict_release() -> None:
    server = fakeredis.FakeServer()
    a, b = _store(server), _store(server)

    assert a.reserve("k", "fp1").status is ReservationStatus.NEW
    assert b.reserve("k", "fp1").status is ReservationStatus.IN_FLIGHT
    assert b.reserve("k", "fp2").status is ReservationStatus.CONFLICT

    a.complete("k", "stored")
    replay = b.reserve("k", "fp1")
    assert replay.status is ReservationStatus.REPLAY
    assert replay.result == "stored"
    assert b.reserve("k", "fp2").status is ReservationStatus.CONFLICT

    assert a.reserve("j", "fp").status is ReservationStatus.NEW
    a.release("j")
    assert b.reserve("j", "fp").status is ReservationStatus.NEW
    b.complete("j", "x")
    a.release("j")  # releasing a completed key must not drop its result
    assert a.reserve("j", "fp").status is ReservationStatus.REPLAY


def test_concurrent_same_key_across_stores_has_exactly_one_winner() -> None:
    server = fakeredis.FakeServer()
    stores = [_store(server) for _ in range(4)]
    barrier = threading.Barrier(16)
    statuses: list[ReservationStatus] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()
        status = stores[i % 4].reserve("same", "fp").status
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses.count(ReservationStatus.NEW) == 1
    assert statuses.count(ReservationStatus.IN_FLIGHT) == 15


def test_quiz_attempt_use_case_over_shared_store_records_one_attempt() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    store = Redis_IdempotencyStore(
        client, encode=encode_attempt_result, decode=decode_attempt_result
    )
    catalog = In_Memory_CatalogRepository()
    catalog.add_quiz(
        QuizRecord(
            stable_id="qz",
            lesson_id="les",
            questions=(
                QuestionRecord(
                    stable_id="qn",
                    options=(
                        QuestionOption(option_id=1, is_correct=True),
                        QuestionOption(option_id=2, is_correct=False),
                    ),
                    concept_ids=("con",),
                ),
            ),
            content_version="v1",
        )
    )
    attempts = In_Memory_AttemptRepository()
    use_case = AttemptQuizUseCase(
        catalog=catalog,
        attempts=attempts,
        mastery=In_Memory_MasteryRepository(),
        reviews=In_Memory_ReviewRepository(),
        idempotency=store,
        clock=Test_Clock(start=datetime(2024, 6, 1, tzinfo=UTC)),
        ids=Sequential_IdGenerator("id"),
    )

    def req(sel: int) -> AttemptQuizRequest:
        return AttemptQuizRequest(
            user_id="u", quiz_id="qz", selections={"qn": sel}, idempotency_key="k"
        )

    first = use_case.execute(req(1))
    again = use_case.execute(req(1))
    assert again.replayed and again.attempt == first.attempt
    assert again.mastery == first.mastery and again.answers == first.answers
    assert len(attempts.list_quiz_attempts("u", "qz")) == 1

    with pytest.raises(IdempotencyConflictError):
        use_case.execute(req(2))
    assert len(attempts.list_quiz_attempts("u", "qz")) == 1
