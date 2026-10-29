"""Tests for the learning application use cases (task 17.1).

Drives the real use cases (``edu_it_application.learning``) over the real domain
services against the infrastructure fakes and the controllable ``Test_Clock``:
no database, no wall clock. Numbers are read from ``edu_it_domain.config``.

Covers enroll/progress, attempt-quiz (idempotency, cooldown, version-free guards
running before any side effect, anti-farming, hint temper, first review entry),
reveal-hint, run-review (decline below the pool minimum, pass/fail intervals),
and submit-project (rejections record nothing, pending vs reviewed).

**Validates: Requirements 17.1, 17.2, 18.1, 18.3, 18.4, 18.5, 20.3, 20.4**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizUseCase,
    EnrollUseCase,
    GetProgressUseCase,
    IdempotencyConflictError,
    InsufficientReviewPoolError,
    InvalidProjectSubmissionError,
    NotEnrolledError,
    NotFoundError,
    QuizInCooldownError,
    RequestInProgressError,
    RevealHintUseCase,
    ReviewNotAvailableError,
    ReviewNotDueError,
    StartReviewUseCase,
    SubmissionStatus,
    SubmitProjectRequest,
    SubmitProjectUseCase,
    SubmitReviewRequest,
    SubmitReviewUseCase,
)
from edu_it_domain.config import (
    ANTI_FARMING_WINDOW,
    COOLDOWN_INTERVAL,
    FAILED_REVIEW_INTERVAL,
    INITIAL_REVIEW_INTERVAL,
    MASTERY_GAIN_PER_CORRECT_ANSWER,
    MASTERY_THRESHOLDS,
    MIN_ELIGIBLE_QUESTION_COUNT,
    PASSED_REVIEW_INTERVAL,
    REVIEW_PASSED_MASTERY_GAIN,
    REVIEW_QUESTION_COUNT,
)
from edu_it_domain.learning.hints import tempered_gain
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    QuestionOption,
    Rubric,
    RubricCriterion,
)
from edu_it_domain.learning.scoring import ForeignOptionError
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.idempotency import ReservationStatus
from edu_it_domain.ports.progress import MODULE_STATE_LOCKED, MODULE_STATE_UNLOCKED
from edu_it_infrastructure.fakes import (
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_IdempotencyStore,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_TRACK = "trk-linux"
_CONCEPT = "con-perms"
_QUIZ = "qz-perms"
_Q1, _Q2 = "qn-1", "qn-2"
_RIGHT, _WRONG = 1, 2


def _question(stable_id: str, concept: str = _CONCEPT) -> QuestionRecord:
    return QuestionRecord(
        stable_id=stable_id,
        options=(
            QuestionOption(option_id=_RIGHT, is_correct=True),
            QuestionOption(option_id=_WRONG, is_correct=False),
        ),
        concept_ids=(concept,),
    )


class _World:
    """All ports wired as the production composition root would, with fakes."""

    def __init__(self, *, questions: int = 2) -> None:
        self.clock = Test_Clock(start=_T0)
        self.ids = Sequential_IdGenerator("id")
        self.catalog = In_Memory_CatalogRepository()
        self.enrollments = In_Memory_EnrollmentRepository()
        self.progress = In_Memory_ProgressRepository()
        self.mastery = In_Memory_MasteryRepository()
        self.reviews = In_Memory_ReviewRepository()
        self.attempts = In_Memory_AttemptRepository()
        self.idempotency = In_Memory_IdempotencyStore()
        self.reviewer = Fake_ProjectReviewer(default_points=10, pending=True)

        qs = (_Q1, _Q2, "qn-3", "qn-4", "qn-5", "qn-6")[:questions]
        self.catalog.add_quiz(
            QuizRecord(
                stable_id=_QUIZ,
                lesson_id="les-1",
                questions=tuple(_question(q) for q in qs),
                content_version="v1",
            )
        )
        self.catalog.add_module(ModuleRecord(stable_id="mod-1", track_id=_TRACK, ordinal=1))
        self.catalog.add_module(
            ModuleRecord(
                stable_id="mod-2",
                track_id=_TRACK,
                ordinal=2,
                prerequisite_concept_ids=(_CONCEPT,),
            )
        )

    def attempt_quiz(self) -> AttemptQuizUseCase:
        return AttemptQuizUseCase(
            catalog=self.catalog,
            attempts=self.attempts,
            mastery=self.mastery,
            reviews=self.reviews,
            idempotency=self.idempotency,
            clock=self.clock,
            ids=self.ids,
        )

    def submit(self, key: str, selections: dict[str, int | None]) -> AttemptQuizRequest:
        return AttemptQuizRequest(
            user_id=_USER, quiz_id=_QUIZ, selections=selections, idempotency_key=key
        )

    def attempt_count(self) -> int:
        return len(self.attempts.list_quiz_attempts(_USER, _QUIZ))


# --- enroll / progress (Req 17.1, 17.2) ---------------------------------------


def test_enroll_records_enrollment_and_seeds_module_lock_states() -> None:
    w = _World()
    use_case = EnrollUseCase(
        catalog=w.catalog,
        enrollments=w.enrollments,
        progress=w.progress,
        mastery=w.mastery,
        clock=w.clock,
        ids=w.ids,
    )

    result = use_case.execute(_USER, _TRACK)

    assert result.created
    assert result.enrollment.enrolled_at == _T0
    assert w.enrollments.get_for_user_and_track(_USER, _TRACK) == result.enrollment
    states = {
        p.module_id: p.state for p in w.progress.list_module_progress(result.enrollment.stable_id)
    }
    assert states == {"mod-1": MODULE_STATE_UNLOCKED, "mod-2": MODULE_STATE_LOCKED}


def test_enroll_is_idempotent_and_rejects_unknown_track() -> None:
    w = _World()
    use_case = EnrollUseCase(
        catalog=w.catalog,
        enrollments=w.enrollments,
        progress=w.progress,
        mastery=w.mastery,
        clock=w.clock,
        ids=w.ids,
    )
    first = use_case.execute(_USER, _TRACK)
    again = use_case.execute(_USER, _TRACK)

    assert not again.created
    assert again.enrollment == first.enrollment
    assert len(w.enrollments.list_for_user(_USER)) == 1
    with pytest.raises(NotFoundError):
        use_case.execute(_USER, "trk-missing")


def test_progress_derives_lock_state_from_mastery_and_requires_enrollment() -> None:
    w = _World()
    get_progress = GetProgressUseCase(
        catalog=w.catalog,
        enrollments=w.enrollments,
        progress=w.progress,
        mastery=w.mastery,
    )
    with pytest.raises(NotEnrolledError):
        get_progress.execute(_USER, _TRACK)

    EnrollUseCase(
        catalog=w.catalog,
        enrollments=w.enrollments,
        progress=w.progress,
        mastery=w.mastery,
        clock=w.clock,
        ids=w.ids,
    ).execute(_USER, _TRACK)
    before = get_progress.execute(_USER, _TRACK)
    assert [m.state for m in before.modules] == [MODULE_STATE_UNLOCKED, MODULE_STATE_LOCKED]

    # Reaching Practiced unlocks the dependent module (Req 2.2); the stored
    # progress row is stale, proving the state is derived, not read back.
    w.mastery.save(
        Mastery(
            user_id=_USER,
            concept_id=_CONCEPT,
            score=MASTERY_THRESHOLDS.practiced,
            state=MasteryState.PRACTICED,
        )
    )
    after = get_progress.execute(_USER, _TRACK)
    assert [m.state for m in after.modules] == [MODULE_STATE_UNLOCKED, MODULE_STATE_UNLOCKED]
    assert len(after.mastery) == 1


# --- attempt-quiz (Req 18.1, 18.2, 23, 24, 6, 7.2, 9.1) ------------------------


def test_attempt_scores_persists_and_schedules_first_review() -> None:
    w = _World()
    result = w.attempt_quiz().execute(w.submit("k1", {_Q1: _RIGHT, _Q2: _RIGHT}))

    assert result.attempt.score == 100
    assert result.best_attempt == result.attempt and result.attempt.is_best
    assert result.attempt.attempted_at == _T0
    assert [a.counted_for_mastery for a in result.answers] == [True, True]
    assert w.attempt_count() == 1

    # Two counted corrects: 2 * gain, which crosses Practiced and starts review.
    stored = w.mastery.get(_USER, _CONCEPT)
    assert stored is not None
    assert stored.score == 2 * MASTERY_GAIN_PER_CORRECT_ANSWER
    assert stored.state is MasteryState.PRACTICED
    assert len(stored.events) == 1  # one aggregated change, one event
    schedule = w.reviews.get(_USER, _CONCEPT)
    assert schedule is not None
    assert schedule.due_at == _T0 + INITIAL_REVIEW_INTERVAL


def test_same_key_and_body_replays_the_stored_result_and_records_once() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    first = use_case.execute(w.submit("k1", {_Q1: _RIGHT, _Q2: _WRONG}))
    replay = use_case.execute(w.submit("k1", {_Q1: _RIGHT, _Q2: _WRONG}))

    assert not first.replayed and replay.replayed
    assert replay.attempt == first.attempt
    assert w.attempt_count() == 1
    assert len(w.mastery.get(_USER, _CONCEPT).events) == 1  # type: ignore[union-attr]


def test_replay_is_served_even_when_the_quiz_is_now_in_cooldown() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(w.submit("k1", {_Q1: _RIGHT}))
    # The cooldown is active now, yet an identical retry must replay, not fail.
    assert use_case.execute(w.submit("k1", {_Q1: _RIGHT})).replayed


def test_reused_key_with_a_different_body_conflicts_and_records_nothing_more() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(w.submit("k1", {_Q1: _RIGHT}))

    with pytest.raises(IdempotencyConflictError):
        use_case.execute(w.submit("k1", {_Q1: _WRONG}))
    assert w.attempt_count() == 1


def test_a_concurrent_duplicate_in_flight_is_not_processed() -> None:
    w = _World()
    request = w.submit("k1", {_Q1: _RIGHT})
    # Another worker has reserved the key but not finished.
    from edu_it_application.learning.attempt_quiz import _fingerprint

    reservation = w.idempotency.reserve(f"quiz-attempt:{_USER}:k1", _fingerprint(request))
    assert reservation.status is ReservationStatus.NEW

    with pytest.raises(RequestInProgressError):
        w.attempt_quiz().execute(request)
    assert w.attempt_count() == 0


def test_cooldown_rejects_with_attemptable_time_and_records_nothing() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(w.submit("k1", {_Q1: _RIGHT}))
    w.clock.advance(COOLDOWN_INTERVAL - timedelta(seconds=1))

    with pytest.raises(QuizInCooldownError) as raised:
        use_case.execute(w.submit("k2", {_Q1: _RIGHT}))

    assert raised.value.attemptable_at == _T0 + COOLDOWN_INTERVAL
    assert w.attempt_count() == 1
    assert w.mastery.get(_USER, _CONCEPT).score == MASTERY_GAIN_PER_CORRECT_ANSWER  # type: ignore[union-attr]

    # A rejected request must not poison its key: after the cooldown it works.
    w.clock.advance(timedelta(seconds=1))
    assert not use_case.execute(w.submit("k2", {_Q1: _WRONG})).replayed
    assert w.attempt_count() == 2


def test_foreign_option_rejects_the_attempt_and_records_nothing() -> None:
    w = _World()
    with pytest.raises(ForeignOptionError):
        w.attempt_quiz().execute(w.submit("k1", {_Q1: 999}))

    assert w.attempt_count() == 0
    assert w.mastery.get(_USER, _CONCEPT) is None
    assert w.reviews.get(_USER, _CONCEPT) is None


def test_unknown_quiz_is_not_found() -> None:
    w = _World()
    request = AttemptQuizRequest(
        user_id=_USER, quiz_id="qz-nope", selections={}, idempotency_key="k"
    )
    with pytest.raises(NotFoundError):
        w.attempt_quiz().execute(request)


def _edit_quiz_to_v2(w: _World) -> None:
    """Sync an edit that flips which option of Q1 is correct (version v1 -> v2)."""
    flipped = QuestionRecord(
        stable_id=_Q1,
        options=(
            QuestionOption(option_id=_RIGHT, is_correct=False),
            QuestionOption(option_id=_WRONG, is_correct=True),
        ),
        concept_ids=(_CONCEPT,),
    )
    w.catalog.revise_quiz(
        QuizRecord(stable_id=_QUIZ, lesson_id="les-1", questions=(flipped,), content_version="v2")
    )


def test_attempt_records_the_content_version_it_was_evaluated_under() -> None:
    w = _World()
    result = w.attempt_quiz().execute(w.submit("k1", {_Q1: _RIGHT, _Q2: _RIGHT}))
    assert result.attempt.content_version == "v1"


def test_mid_attempt_edit_keeps_the_active_attempt_pinned_to_its_version() -> None:
    w = _World()
    _edit_quiz_to_v2(w)  # the quiz changes after the learner started under v1
    request = AttemptQuizRequest(
        user_id=_USER,
        quiz_id=_QUIZ,
        selections={_Q1: _RIGHT, _Q2: _RIGHT},
        idempotency_key="k1",
        content_version="v1",
    )
    result = w.attempt_quiz().execute(request)

    assert result.attempt.content_version == "v1"
    assert result.attempt.score == 100  # judged against v1, where _RIGHT is correct


def test_new_attempt_after_an_edit_uses_the_new_version_and_keeps_old_rows() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    first = use_case.execute(w.submit("k1", {_Q1: _RIGHT, _Q2: _RIGHT}))
    mastery_before = w.mastery.get(_USER, _CONCEPT)

    _edit_quiz_to_v2(w)
    w.clock.advance(COOLDOWN_INTERVAL)
    second = use_case.execute(w.submit("k2", {_Q1: _RIGHT}))

    assert second.attempt.content_version == "v2"
    assert second.attempt.score == 0  # _RIGHT is no longer correct under v2
    stored = {a.stable_id: a for a in w.attempts.list_quiz_attempts(_USER, _QUIZ)}
    assert stored[first.attempt.stable_id].content_version == "v1"
    assert stored[first.attempt.stable_id].score == 100  # existing row intact (Req 25.3)
    assert w.mastery.get(_USER, _CONCEPT) == mastery_before


def test_unknown_pinned_version_is_not_found_and_records_nothing() -> None:
    w = _World()
    request = AttemptQuizRequest(
        user_id=_USER,
        quiz_id=_QUIZ,
        selections={_Q1: _RIGHT},
        idempotency_key="k1",
        content_version="v-missing",
    )
    with pytest.raises(NotFoundError):
        w.attempt_quiz().execute(request)
    assert w.attempt_count() == 0


def test_reanswer_inside_the_24h_window_earns_no_mastery_but_still_records() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(w.submit("k1", {_Q1: _RIGHT}))
    w.clock.advance(COOLDOWN_INTERVAL)  # past cooldown, inside 24h

    again = use_case.execute(w.submit("k2", {_Q1: _RIGHT}))

    assert [a.counted_for_mastery for a in again.answers] == [False]
    assert again.mastery == ()
    stored = w.mastery.get(_USER, _CONCEPT)
    assert stored is not None and stored.score == MASTERY_GAIN_PER_CORRECT_ANSWER
    assert len(stored.events) == 1
    assert w.attempt_count() == 2  # best attempt / completion still count (Req 6.3)

    # At the exact 24h boundary the standard rules apply again (Req 6.5).
    w.clock.set(_T0 + ANTI_FARMING_WINDOW)
    counted = use_case.execute(w.submit("k3", {_Q1: _RIGHT}))
    assert [a.counted_for_mastery for a in counted.answers] == [True]
    assert w.mastery.get(_USER, _CONCEPT).score == 2 * MASTERY_GAIN_PER_CORRECT_ANSWER  # type: ignore[union-attr]


def test_hint_revealed_before_submission_tempers_that_attempts_gain() -> None:
    w = _World()
    RevealHintUseCase(catalog=w.catalog, mastery=w.mastery, clock=w.clock).execute(
        _USER, _QUIZ, _Q1
    )
    w.clock.advance(timedelta(minutes=1))

    w.attempt_quiz().execute(w.submit("k1", {_Q1: _RIGHT}))

    expected = tempered_gain(MASTERY_GAIN_PER_CORRECT_ANSWER, hint_revealed_before_submission=True)
    assert expected < MASTERY_GAIN_PER_CORRECT_ANSWER
    assert w.mastery.get(_USER, _CONCEPT).score == expected  # type: ignore[union-attr]


def test_hint_revealed_after_submission_does_not_retemper_it() -> None:
    w = _World()
    use_case = w.attempt_quiz()
    use_case.execute(w.submit("k1", {_Q1: _RIGHT}))
    w.clock.advance(timedelta(minutes=1))
    RevealHintUseCase(catalog=w.catalog, mastery=w.mastery, clock=w.clock).execute(
        _USER, _QUIZ, _Q1
    )

    assert w.mastery.get(_USER, _CONCEPT).score == MASTERY_GAIN_PER_CORRECT_ANSWER  # type: ignore[union-attr]


# --- reveal-hint (Req 18.3, 7.1) ----------------------------------------------


def test_reveal_hint_records_one_reveal_one_event_and_progressive_levels() -> None:
    w = _World()
    use_case = RevealHintUseCase(catalog=w.catalog, mastery=w.mastery, clock=w.clock)

    first = use_case.execute(_USER, _QUIZ, _Q1)
    second = use_case.execute(_USER, _QUIZ, _Q1)

    assert (first.hint_level, second.hint_level) == (1, 2)
    assert len(w.mastery.list_hint_reveals(_USER)) == 2
    assert first.reveal.revealed_at == _T0
    events = w.mastery.get(_USER, _CONCEPT).events  # type: ignore[union-attr]
    assert len(events) == 2 and {e.source for e in events} == {"hint"}
    assert all(e.delta == 0 for e in events)  # reveals cannot farm mastery

    with pytest.raises(NotFoundError):
        use_case.execute(_USER, _QUIZ, "qn-nope")
    with pytest.raises(NotFoundError):
        use_case.execute(_USER, "qz-nope", _Q1)


# --- run-review (Req 18.4, 9, 28) ---------------------------------------------


def _practiced_world(*, questions: int) -> _World:
    """A learner whose concept reached Practiced at ``_T0`` (review due a week later)."""
    w = _World(questions=questions)
    w.attempt_quiz().execute(w.submit("seed", {_Q1: _RIGHT, _Q2: _RIGHT}))
    assert w.reviews.get(_USER, _CONCEPT) is not None
    return w


def _review_use_cases(w: _World) -> tuple[StartReviewUseCase, SubmitReviewUseCase]:
    return (
        StartReviewUseCase(catalog=w.catalog, reviews=w.reviews, clock=w.clock),
        SubmitReviewUseCase(catalog=w.catalog, reviews=w.reviews, mastery=w.mastery, clock=w.clock),
    )


def test_review_below_the_minimum_pool_is_declined_without_advancing_the_schedule() -> None:
    w = _practiced_world(questions=2)  # pool of 2 < minimum
    assert MIN_ELIGIBLE_QUESTION_COUNT > 2
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    start, submit = _review_use_cases(w)
    before = w.reviews.get(_USER, _CONCEPT)

    with pytest.raises(InsufficientReviewPoolError):
        start.execute(_USER, _CONCEPT)
    with pytest.raises(InsufficientReviewPoolError):
        submit.execute(SubmitReviewRequest(_USER, _CONCEPT, {_Q1: _RIGHT}))

    assert w.reviews.get(_USER, _CONCEPT) == before
    assert len(w.reviews.list_for_user(_USER)) == 1


def test_review_unavailable_before_practiced_and_not_due_before_its_time() -> None:
    w = _World(questions=MIN_ELIGIBLE_QUESTION_COUNT)
    start, _ = _review_use_cases(w)
    with pytest.raises(ReviewNotAvailableError):
        start.execute(_USER, _CONCEPT)

    w = _practiced_world(questions=MIN_ELIGIBLE_QUESTION_COUNT)
    start, _ = _review_use_cases(w)
    with pytest.raises(ReviewNotDueError) as raised:
        start.execute(_USER, _CONCEPT)
    assert raised.value.due_at == _T0 + INITIAL_REVIEW_INTERVAL


def test_passed_review_lengthens_interval_and_earns_the_review_gain() -> None:
    w = _practiced_world(questions=MIN_ELIGIBLE_QUESTION_COUNT)
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    start, submit = _review_use_cases(w)

    questions = start.execute(_USER, _CONCEPT)
    assert len(questions) == min(REVIEW_QUESTION_COUNT, MIN_ELIGIBLE_QUESTION_COUNT)
    score_before = w.mastery.get(_USER, _CONCEPT).score  # type: ignore[union-attr]

    result = submit.execute(
        SubmitReviewRequest(_USER, _CONCEPT, {q.stable_id: _RIGHT for q in questions})
    )

    assert result.passed and result.score == 100
    assert result.schedule.interval == PASSED_REVIEW_INTERVAL
    assert result.schedule.due_at == w.clock.now() + PASSED_REVIEW_INTERVAL
    assert result.mastery.score == score_before + REVIEW_PASSED_MASTERY_GAIN
    assert w.reviews.get(_USER, _CONCEPT) == result.schedule
    assert len(w.reviews.list_for_user(_USER)) == 1


def test_failed_review_shortens_interval_without_a_mastery_gain() -> None:
    w = _practiced_world(questions=MIN_ELIGIBLE_QUESTION_COUNT)
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    start, submit = _review_use_cases(w)
    questions = start.execute(_USER, _CONCEPT)
    score_before = w.mastery.get(_USER, _CONCEPT).score  # type: ignore[union-attr]

    result = submit.execute(
        SubmitReviewRequest(_USER, _CONCEPT, {q.stable_id: _WRONG for q in questions})
    )

    assert not result.passed
    assert result.schedule.interval == FAILED_REVIEW_INTERVAL
    assert result.mastery.score == score_before


def test_review_rejects_selections_outside_the_session() -> None:
    w = _practiced_world(questions=MIN_ELIGIBLE_QUESTION_COUNT)
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    _, submit = _review_use_cases(w)
    before = w.reviews.get(_USER, _CONCEPT)

    with pytest.raises(ForeignOptionError):
        submit.execute(SubmitReviewRequest(_USER, _CONCEPT, {"qn-elsewhere": _RIGHT}))
    assert w.reviews.get(_USER, _CONCEPT) == before


# --- submit-project (Req 18.5, 20) --------------------------------------------

_PROJECT = "prj-harden"


def _project_world(*, pending: bool) -> tuple[_World, SubmitProjectUseCase]:
    w = _World()
    w.catalog.add_rubric(
        Rubric(
            stable_id="rub-1",
            project_id=_PROJECT,
            criteria=(
                RubricCriterion(stable_id="cr-correct", max_points=10),
                RubricCriterion(stable_id="cr-least", max_points=10),
            ),
        )
    )
    w.reviewer = Fake_ProjectReviewer(default_points=10, pending=pending)
    return w, SubmitProjectUseCase(
        catalog=w.catalog, attempts=w.attempts, reviewer=w.reviewer, ids=w.ids
    )


def _project_request(**overrides: object) -> SubmitProjectRequest:
    fields: dict[str, object] = {
        "user_id": _USER,
        "project_id": _PROJECT,
        "self_assessment": {"cr-correct": 8, "cr-least": 9},
        "evidence": ["https://example.test/evidence"],
    }
    fields.update(overrides)
    return SubmitProjectRequest(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"self_assessment": {}}, InvalidProjectSubmissionError.MISSING_SELF_ASSESSMENT),
        (
            {"self_assessment": {"cr-correct": 5}},
            InvalidProjectSubmissionError.INCOMPLETE_SELF_ASSESSMENT,
        ),
        (
            {"self_assessment": {"cr-correct": 5, "cr-least": 99}},
            InvalidProjectSubmissionError.INCOMPLETE_SELF_ASSESSMENT,
        ),
        ({"evidence": []}, InvalidProjectSubmissionError.MISSING_EVIDENCE),
        ({"evidence": ["  "]}, InvalidProjectSubmissionError.MISSING_EVIDENCE),
    ],
)
def test_incomplete_submission_is_rejected_and_records_nothing(
    overrides: dict[str, object], reason: str
) -> None:
    w, use_case = _project_world(pending=False)

    with pytest.raises(InvalidProjectSubmissionError) as raised:
        use_case.execute(_project_request(**overrides))

    assert raised.value.reason == reason
    assert w.attempts.list_project_submissions(_USER) == ()


def test_complete_submission_is_recorded_pending_until_the_reviewer_completes() -> None:
    w, use_case = _project_world(pending=True)

    result = use_case.execute(_project_request())

    assert result.status is SubmissionStatus.PENDING
    assert result.submission.review_outcome is None
    stored = w.attempts.get_project_submission(result.submission.stable_id)
    assert stored is not None and stored.review_outcome is None
    assert w.attempts.list_project_submissions(_USER) == (stored,)


def test_complete_submission_gets_a_review_outcome_once_the_reviewer_completes() -> None:
    w, use_case = _project_world(pending=False)

    result = use_case.execute(_project_request())

    assert result.status is SubmissionStatus.REVIEWED
    assert result.submission.review_outcome == "passed"
    stored = w.attempts.get_project_submission(result.submission.stable_id)
    assert stored is not None and stored.review_outcome == "passed"


def test_submission_for_a_project_without_a_rubric_is_not_found() -> None:
    _, use_case = _project_world(pending=False)
    with pytest.raises(NotFoundError):
        use_case.execute(_project_request(project_id="prj-missing"))
