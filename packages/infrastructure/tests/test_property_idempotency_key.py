"""Property test for Idempotency-Key handling on quiz-attempt submission (task 18.6).

Feature: learning-core, Property 22: Idempotency-Key makes a submission take
effect at most once

**Validates: Requirements 18.1, 18.2, 23.1, 23.2, 23.3**
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import fakeredis
from edu_it_api.errors.learning import IDEMPOTENCY_CONFLICT
from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    IdempotencyConflictError,
)
from edu_it_application.learning.attempt_codec import (
    decode_attempt_result,
    encode_attempt_result,
)
from edu_it_application.learning.attempt_quiz import AttemptQuizResult
from edu_it_application.learning.errors import RequestInProgressError
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_IdempotencyStore,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_keys = st.text(alphabet="abcdefghijklmnop0123456789-", min_size=1, max_size=24)
_selection = st.integers(min_value=1, max_value=2)
_backend = st.sampled_from(["memory", "redis"])


def _build(backend: str) -> tuple[AttemptQuizUseCase, In_Memory_AttemptRepository]:
    if backend == "redis":
        client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        store: Redis_IdempotencyStore | In_Memory_IdempotencyStore = Redis_IdempotencyStore(
            client, encode=encode_attempt_result, decode=decode_attempt_result
        )
    else:
        store = In_Memory_IdempotencyStore()
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
    return use_case, attempts


def _req(key: str, selection: int) -> AttemptQuizRequest:
    return AttemptQuizRequest(
        user_id="u", quiz_id="qz", selections={"qn": selection}, idempotency_key=key
    )


@settings(max_examples=40, deadline=None)
@given(backend=_backend, key=_keys, selection=_selection, repeats=st.integers(2, 6))
def test_property_22_same_key_same_body_takes_effect_once(
    backend: str, key: str, selection: int, repeats: int
) -> None:
    """Feature: learning-core, Property 22: same key + same body => one attempt, result replayed."""
    use_case, attempts = _build(backend)
    first = use_case.execute(_req(key, selection))
    assert not first.replayed
    for _ in range(repeats):
        again = use_case.execute(_req(key, selection))
        assert again.replayed
        assert again.attempt == first.attempt
        assert again.mastery == first.mastery
        assert again.answers == first.answers
    assert len(attempts.list_quiz_attempts("u", "qz")) == 1


@settings(max_examples=15, deadline=None)
@given(backend=_backend, key=_keys, selection=_selection, workers=st.integers(2, 8))
def test_property_22_concurrent_same_key_takes_effect_at_most_once(
    backend: str, key: str, selection: int, workers: int
) -> None:
    """Feature: learning-core, Property 22: concurrent same-key submissions record <=1 attempt."""
    use_case, attempts = _build(backend)
    barrier = threading.Barrier(workers)
    results: list[AttemptQuizResult | RequestInProgressError] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            outcome: AttemptQuizResult | RequestInProgressError = use_case.execute(
                _req(key, selection)
            )
        except RequestInProgressError as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == workers
    fresh = [r for r in results if isinstance(r, AttemptQuizResult) and not r.replayed]
    assert len(fresh) == 1
    assert len(attempts.list_quiz_attempts("u", "qz")) == 1
    # Every other caller either replayed the stored result or was told it is in progress.
    for r in results:
        if r is not fresh[0] and isinstance(r, AttemptQuizResult):
            assert r.replayed and r.attempt == fresh[0].attempt
    # After completion every further call replays the first result.
    after = use_case.execute(_req(key, selection))
    assert after.replayed and after.attempt == fresh[0].attempt


@settings(max_examples=40, deadline=None)
@given(backend=_backend, key=_keys, first_sel=_selection)
def test_property_22_reused_key_with_different_body_conflicts(
    backend: str, key: str, first_sel: int
) -> None:
    """Feature: learning-core, Property 22: reused key + different body => conflict (409)."""
    use_case, attempts = _build(backend)
    use_case.execute(_req(key, first_sel))
    other = 2 if first_sel == 1 else 1
    try:
        use_case.execute(_req(key, other))
    except IdempotencyConflictError:
        pass
    else:
        raise AssertionError("expected IdempotencyConflictError")
    assert IDEMPOTENCY_CONFLICT.status == 409
    assert len(attempts.list_quiz_attempts("u", "qz")) == 1
