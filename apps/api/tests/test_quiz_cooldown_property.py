"""Property test for quiz cooldown rejection (task 18.8).

Feature: learning-core, Property 23: Submissions during cooldown are rejected
without recording

For any elapsed time since the previous attempt that falls inside the cooldown
window, the submission is rejected with ``QuizInCooldownError`` (rendered as a
429 problem at the HTTP boundary) and no attempt is recorded; at or beyond the
window boundary it is accepted and recorded.

Uses Hypothesis, the pinned Python property-based testing library.

**Validates: Requirements 24.1, 24.2**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    QuizInCooldownError,
)
from edu_it_domain.config import COOLDOWN_INTERVAL
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
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

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_QUIZ = "qz-1"
_INTERVAL_S = int(COOLDOWN_INTERVAL.total_seconds())


def _setup() -> tuple[AttemptQuizUseCase, In_Memory_AttemptRepository, Test_Clock]:
    clock = Test_Clock(start=_T0)
    catalog = In_Memory_CatalogRepository()
    attempts = In_Memory_AttemptRepository()
    catalog.add_quiz(
        QuizRecord(
            stable_id=_QUIZ,
            lesson_id="les-1",
            questions=(
                QuestionRecord(
                    stable_id="qn-1",
                    options=(
                        QuestionOption(option_id=1, is_correct=True),
                        QuestionOption(option_id=2, is_correct=False),
                    ),
                    concept_ids=("con-1",),
                ),
            ),
            content_version="v1",
        )
    )
    use_case = AttemptQuizUseCase(
        catalog=catalog,
        attempts=attempts,
        mastery=In_Memory_MasteryRepository(),
        reviews=In_Memory_ReviewRepository(),
        idempotency=In_Memory_IdempotencyStore(),
        clock=clock,
        ids=Sequential_IdGenerator("id"),
    )
    return use_case, attempts, clock


def _request(key: str) -> AttemptQuizRequest:
    return AttemptQuizRequest(
        user_id=_USER, quiz_id=_QUIZ, selections={"qn-1": 1}, idempotency_key=key
    )


@settings(max_examples=100, deadline=None)
@given(elapsed=st.integers(min_value=0, max_value=_INTERVAL_S - 1))
def test_submission_inside_cooldown_is_rejected_without_recording(elapsed: int) -> None:
    """Feature: learning-core, Property 23: Submissions during cooldown are
    rejected without recording.

    **Validates: Requirements 24.1, 24.2**
    """
    use_case, attempts, clock = _setup()
    use_case.execute(_request("k-1"))
    clock.advance(timedelta(seconds=elapsed))

    with pytest.raises(QuizInCooldownError) as info:
        use_case.execute(_request("k-2"))

    assert info.value.attemptable_at == _T0 + COOLDOWN_INTERVAL
    assert len(attempts.list_quiz_attempts(_USER, _QUIZ)) == 1


@settings(max_examples=100, deadline=None)
@given(elapsed=st.integers(min_value=_INTERVAL_S, max_value=_INTERVAL_S * 5))
def test_submission_outside_cooldown_is_accepted_and_recorded(elapsed: int) -> None:
    """Feature: learning-core, Property 23: Submissions during cooldown are
    rejected without recording (outside the window the submission is accepted).

    **Validates: Requirements 24.1, 24.2**
    """
    use_case, attempts, clock = _setup()
    use_case.execute(_request("k-1"))
    clock.advance(timedelta(seconds=elapsed))

    use_case.execute(_request("k-2"))

    assert len(attempts.list_quiz_attempts(_USER, _QUIZ)) == 2
