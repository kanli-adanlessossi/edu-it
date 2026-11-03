"""Quiz and review answers append an ``item_observation`` (ai-content task 19.2).

Drives the real ``AttemptQuizUseCase`` and ``SubmitReviewUseCase`` over the fakes with an
``ObservationRecorder``. Elapsed time is optional and clamped to ``ITEM_ELAPSED_MS_MAX``;
a served variant is observed as the variant; a recorder failure never fails the answer.

Validates: Requirements 19.1, 21.4
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    SubmitReviewRequest,
    SubmitReviewUseCase,
)
from edu_it_domain.ai.serving import ResolvedQuestion
from edu_it_domain.config import (
    INITIAL_REVIEW_INTERVAL,
    ITEM_ELAPSED_MS_MAX,
    MIN_ELIGIBLE_QUESTION_COUNT,
)
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import NewObservation, ObservationRecorder
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_IdempotencyStore,
    In_Memory_ItemQualityRepository,
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_CONCEPT = "con-perms"
_QUIZ = "qz-perms"
_RIGHT, _WRONG = 1, 2
_QUESTIONS = tuple(f"qn-{n}" for n in range(1, MIN_ELIGIBLE_QUESTION_COUNT + 1))


def _question(stable_id: str) -> QuestionRecord:
    return QuestionRecord(
        stable_id=stable_id,
        options=(
            QuestionOption(option_id=_RIGHT, is_correct=True),
            QuestionOption(option_id=_WRONG, is_correct=False),
        ),
        concept_ids=(_CONCEPT,),
    )


class _World:
    def __init__(self, *, recorder: ObservationRecorder | None = None) -> None:
        self.clock = Test_Clock(start=_T0)
        self.quality = In_Memory_ItemQualityRepository()
        self.recorder: ObservationRecorder | None = self.quality if recorder is None else recorder
        self.catalog = In_Memory_CatalogRepository()
        self.mastery = In_Memory_MasteryRepository()
        self.reviews = In_Memory_ReviewRepository()
        self.attempts = In_Memory_AttemptRepository()
        self.catalog.add_quiz(
            QuizRecord(
                stable_id=_QUIZ,
                lesson_id="les-1",
                questions=tuple(_question(q) for q in _QUESTIONS),
                content_version="v1",
            )
        )
        self.catalog.add_module(ModuleRecord(stable_id="mod-1", track_id="trk-1", ordinal=1))
        self.ids = Sequential_IdGenerator("id")
        self.idempotency = In_Memory_IdempotencyStore()

    def attempt_quiz(self) -> AttemptQuizUseCase:
        return AttemptQuizUseCase(
            catalog=self.catalog,
            attempts=self.attempts,
            mastery=self.mastery,
            reviews=self.reviews,
            idempotency=self.idempotency,
            clock=self.clock,
            ids=self.ids,
            observations=self.recorder,
        )

    def submit_review(self, resolver: Any = None) -> SubmitReviewUseCase:
        return SubmitReviewUseCase(
            catalog=self.catalog,
            reviews=self.reviews,
            mastery=self.mastery,
            clock=self.clock,
            resolver=resolver,
            observations=self.recorder,
        )


def _request(selections: dict[str, int | None], elapsed: dict[str, int] | None = None) -> Any:
    return AttemptQuizRequest(
        user_id=_USER,
        quiz_id=_QUIZ,
        selections=selections,
        idempotency_key="k1",
        elapsed_ms=elapsed or {},
    )


def test_quiz_appends_one_observation_per_answered_question() -> None:
    """Validates: Requirements 19.1"""
    w = _World()
    w.attempt_quiz().execute(
        _request(
            {_QUESTIONS[0]: _RIGHT, _QUESTIONS[1]: _WRONG, _QUESTIONS[2]: None},
            {_QUESTIONS[0]: 4200},
        )
    )
    got = w.quality.recorded_for(_USER)
    assert [(o.key, o.correct, o.elapsed_ms, o.variant_ref, o.observed_at) for o in got] == [
        (ItemKey(ItemKind.QUESTION, _QUESTIONS[0]), True, 4200, None, _T0),
        (ItemKey(ItemKind.QUESTION, _QUESTIONS[1]), False, None, None, _T0),
    ]
    assert [o.id for o in w.quality.observations_after(0, 10)] == [1, 2]


def test_replayed_quiz_submission_records_nothing_more() -> None:
    """Validates: Requirements 19.1"""
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(_request({_QUESTIONS[0]: _RIGHT}))
    assert use_case.execute(_request({_QUESTIONS[0]: _RIGHT})).replayed
    assert len(w.quality.recorded_for(_USER)) == 1


def test_a_failing_recorder_never_fails_the_answer() -> None:
    """Validates: Requirements 19.1"""

    class _Broken:
        def record(self, obs: NewObservation) -> None:
            raise RuntimeError("database unavailable")

    w = _World(recorder=_Broken())
    result = w.attempt_quiz().execute(_request({_QUESTIONS[0]: _RIGHT}))
    assert result.attempt.score > 0
    assert len(w.attempts.list_quiz_attempts(_USER, _QUIZ)) == 1


def test_use_cases_work_without_a_recorder() -> None:
    w = _World()
    w.recorder = None
    assert w.attempt_quiz().execute(_request({_QUESTIONS[0]: _RIGHT})).attempt.score > 0


def _practiced_review_world() -> _World:
    w = _World()
    w.attempt_quiz().execute(_request({q: _RIGHT for q in _QUESTIONS}))
    assert w.reviews.get(_USER, _CONCEPT) is not None
    w.quality = In_Memory_ItemQualityRepository()  # forget the seeding attempt
    w.recorder = w.quality
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    return w


def test_review_appends_one_observation_per_answered_question() -> None:
    """Validates: Requirements 19.1"""
    w = _practiced_review_world()
    selections: dict[str, int | None] = {q: _RIGHT for q in _QUESTIONS}
    selections[_QUESTIONS[0]] = _WRONG
    selections[_QUESTIONS[1]] = None
    w.submit_review().execute(
        SubmitReviewRequest(_USER, _CONCEPT, selections, elapsed_ms={_QUESTIONS[2]: 9000})
    )
    got = {o.key.stable_id: o for o in w.quality.recorded_for(_USER)}
    assert _QUESTIONS[1] not in got
    assert len(got) == len(_QUESTIONS) - 1
    assert got[_QUESTIONS[0]].correct is False
    assert got[_QUESTIONS[2]].correct is True and got[_QUESTIONS[2]].elapsed_ms == 9000
    assert {o.key.kind for o in got.values()} == {ItemKind.QUESTION}


def test_a_served_variant_is_observed_as_the_variant() -> None:
    """Validates: Requirements 19.1"""
    w = _practiced_review_world()
    catalog = w.catalog

    class _VariantResolver:
        def resolve_review_question(
            self, *, concept_id: str, parent_question_id: str, **_: object
        ) -> ResolvedQuestion:
            parent = next(
                q
                for q in catalog.list_questions_for_concept(concept_id)
                if q.stable_id == parent_question_id
            )
            return ResolvedQuestion(parent, f"var-{parent_question_id}@v2")

    w.submit_review(_VariantResolver()).execute(
        SubmitReviewRequest(
            _USER,
            _CONCEPT,
            {q: _RIGHT for q in _QUESTIONS},
            variant_refs={q: f"var-{q}@v2" for q in _QUESTIONS},
        )
    )
    got = w.quality.recorded_for(_USER)
    assert got
    for o in got:
        assert o.key.kind is ItemKind.VARIANT
        assert o.key.stable_id.startswith("var-")
        assert o.variant_ref == f"{o.key.stable_id}@v2"


@settings(max_examples=50)
@given(elapsed=st.integers(min_value=-10_000_000, max_value=10_000_000))
def test_elapsed_time_is_clamped_to_the_configured_maximum(elapsed: int) -> None:
    """Validates: Requirements 19.1"""
    w = _World()
    w.attempt_quiz().execute(
        AttemptQuizRequest(
            user_id=_USER,
            quiz_id=_QUIZ,
            selections={_QUESTIONS[0]: _RIGHT},
            idempotency_key="k",
            elapsed_ms={_QUESTIONS[0]: elapsed},
        )
    )
    (obs,) = w.quality.recorded_for(_USER)
    assert obs.elapsed_ms == min(max(elapsed, 0), ITEM_ELAPSED_MS_MAX)
