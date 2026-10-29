"""Unit tests for the in-memory learning repository fakes (task 1.4, Req 1.4, 5.2, 11.4).

These prove the six in-memory learning fakes satisfy their ``@runtime_checkable``
domain ports (the substitutability the fakes exist for, Req 5.2) and enforce the
invariants the port docstrings specify:

- catalog: duplicate ``stable_id`` rejection, ordinal-ordered module listing;
- enrollment: duplicate ``stable_id`` and ``(user, track)`` rejection;
- progress: in-place module-progress upsert, idempotent lesson completion;
- mastery: append-only ``mastery_event`` history, append-only hint reveals;
- review: single entry per ``(user, concept)`` that resumes not duplicates,
  append-only attempt history;
- attempts: duplicate ``stable_id`` rejection, oldest-first ordering, pending →
  reviewed submission update.

``Test_Clock`` drives every instant (Req 11.4).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    MasteryEvent,
    MasteryState,
    ProjectSubmissionRef,
    QuestionOption,
    QuizAttempt,
    ReviewAttempt,
    ReviewSchedule,
    Rubric,
    RubricCriterion,
)
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import (
    CatalogRepository,
    ConceptRecord,
    ModuleRecord,
    QuestionRecord,
    QuizRecord,
)
from edu_it_domain.ports.enrollment import EnrollmentRecord, EnrollmentRepository
from edu_it_domain.ports.mastery import HintRevealRecord, MasteryRepository
from edu_it_domain.ports.progress import (
    MODULE_STATE_IN_PROGRESS,
    MODULE_STATE_LOCKED,
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)
from edu_it_domain.ports.review import ReviewRepository
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    Test_Clock,
)

_CLOCK = Test_Clock()
_T0 = _CLOCK.now()


# --- Catalog (Req 1.2) -------------------------------------------------------


def test_catalog_satisfies_its_port() -> None:
    catalog: CatalogRepository = In_Memory_CatalogRepository()
    assert isinstance(catalog, CatalogRepository)


def test_catalog_lists_modules_for_track_in_ordinal_order() -> None:
    catalog = In_Memory_CatalogRepository()
    catalog.add_module(ModuleRecord(stable_id="m2", track_id="t1", ordinal=2))
    catalog.add_module(ModuleRecord(stable_id="m1", track_id="t1", ordinal=1))
    catalog.add_module(ModuleRecord(stable_id="m-other", track_id="t2", ordinal=1))

    modules = catalog.list_modules_for_track("t1")
    assert tuple(m.stable_id for m in modules) == ("m1", "m2")
    assert catalog.get_module("m1") is not None
    assert catalog.get_module("missing") is None


def test_catalog_rejects_duplicate_stable_ids() -> None:
    catalog = In_Memory_CatalogRepository()
    catalog.add_module(ModuleRecord(stable_id="m1", track_id="t1", ordinal=1))
    catalog.add_concept(ConceptRecord(stable_id="c1"))
    catalog.add_quiz(QuizRecord(stable_id="q1", lesson_id="l1"))

    with pytest.raises(ValueError, match="module"):
        catalog.add_module(ModuleRecord(stable_id="m1", track_id="t1", ordinal=9))
    with pytest.raises(ValueError, match="concept"):
        catalog.add_concept(ConceptRecord(stable_id="c1"))
    with pytest.raises(ValueError, match="quiz"):
        catalog.add_quiz(QuizRecord(stable_id="q1", lesson_id="l9"))


def test_catalog_resolves_concepts_quizzes_and_rubrics() -> None:
    catalog = In_Memory_CatalogRepository()
    catalog.add_concept(ConceptRecord(stable_id="c1", requires=("c0",)))
    catalog.add_quiz(
        QuizRecord(
            stable_id="q1",
            lesson_id="l1",
            questions=(
                QuestionRecord(
                    stable_id="qn1",
                    options=(QuestionOption(option_id=1, is_correct=True),),
                    concept_ids=("c1",),
                ),
            ),
        )
    )
    rubric = Rubric(
        stable_id="rub1",
        project_id="p1",
        criteria=(RubricCriterion(stable_id="crit1", max_points=10),),
    )
    catalog.add_rubric(rubric)

    assert catalog.get_concept("c1").requires == ("c0",)  # type: ignore[union-attr]
    assert catalog.get_quiz("q1").lesson_id == "l1"  # type: ignore[union-attr]
    assert catalog.get_rubric_for_project("p1") == rubric
    assert catalog.get_rubric_for_project("missing") is None

    with pytest.raises(ValueError, match="rubric"):
        catalog.add_rubric(Rubric(stable_id="rub2", project_id="p1"))


# --- Enrollment (Req 17.1) ---------------------------------------------------


def test_enrollment_satisfies_its_port() -> None:
    enrollments: EnrollmentRepository = In_Memory_EnrollmentRepository()
    assert isinstance(enrollments, EnrollmentRepository)


def test_enrollment_rejects_duplicate_stable_id_and_user_track_pair() -> None:
    enrollments = In_Memory_EnrollmentRepository()
    enrollments.add(EnrollmentRecord(stable_id="e1", user_id="u1", track_id="t1", enrolled_at=_T0))

    assert enrollments.get("e1") is not None
    assert enrollments.get_for_user_and_track("u1", "t1") is not None
    assert enrollments.get_for_user_and_track("u1", "t2") is None

    # Reused stable_id is rejected.
    with pytest.raises(ValueError, match="stable_id"):
        enrollments.add(
            EnrollmentRecord(stable_id="e1", user_id="u9", track_id="t9", enrolled_at=_T0)
        )
    # A second enrollment of the same learner in the same track is rejected.
    with pytest.raises(ValueError, match="already enrolled"):
        enrollments.add(
            EnrollmentRecord(stable_id="e2", user_id="u1", track_id="t1", enrolled_at=_T0)
        )


def test_enrollment_lists_for_user() -> None:
    enrollments = In_Memory_EnrollmentRepository()
    enrollments.add(EnrollmentRecord(stable_id="e1", user_id="u1", track_id="t1", enrolled_at=_T0))
    enrollments.add(EnrollmentRecord(stable_id="e2", user_id="u1", track_id="t2", enrolled_at=_T0))
    enrollments.add(EnrollmentRecord(stable_id="e3", user_id="u2", track_id="t1", enrolled_at=_T0))
    assert {e.stable_id for e in enrollments.list_for_user("u1")} == {"e1", "e2"}


# --- Progress (Req 17.2) -----------------------------------------------------


def test_progress_satisfies_its_port() -> None:
    progress: ProgressRepository = In_Memory_ProgressRepository()
    assert isinstance(progress, ProgressRepository)


def test_progress_module_upsert_updates_in_place() -> None:
    progress = In_Memory_ProgressRepository()
    progress.upsert_module_progress(
        ModuleProgressRecord(enrollment_id="e1", module_id="m1", state=MODULE_STATE_LOCKED)
    )
    progress.upsert_module_progress(
        ModuleProgressRecord(
            enrollment_id="e1",
            module_id="m1",
            state=MODULE_STATE_IN_PROGRESS,
            tested_out=True,
        )
    )
    rows = progress.list_module_progress("e1")
    assert len(rows) == 1
    assert rows[0].state == MODULE_STATE_IN_PROGRESS
    assert rows[0].tested_out is True


def test_progress_lesson_completion_is_idempotent_and_preserves_the_first() -> None:
    progress = In_Memory_ProgressRepository()
    first = LessonCompletionRecord(module_id="m1", lesson_id="l1", completed_at=_T0)
    later = LessonCompletionRecord(
        module_id="m1", lesson_id="l1", completed_at=_T0 + timedelta(days=5)
    )
    progress.add_lesson_completion(first)
    progress.add_lesson_completion(later)  # idempotent: no duplicate, first preserved

    completions = progress.list_lesson_completions("m1")
    assert len(completions) == 1
    assert completions[0].completed_at == _T0


# --- Mastery (Req 5.1, 5.7, 7.1) ---------------------------------------------


def test_mastery_satisfies_its_port() -> None:
    mastery: MasteryRepository = In_Memory_MasteryRepository()
    assert isinstance(mastery, MasteryRepository)


def _mastery(score: int, events: tuple[MasteryEvent, ...]) -> Mastery:
    return Mastery(
        user_id="u1",
        concept_id="c1",
        score=score,
        state=MasteryState.PRACTICED,
        events=events,
    )


def test_mastery_save_is_append_only_for_events() -> None:
    mastery = In_Memory_MasteryRepository()
    e1 = MasteryEvent(delta=30, source="quiz", occurred_at=_T0)
    e2 = MasteryEvent(delta=10, source="review", occurred_at=_T0 + timedelta(days=1))

    mastery.save(_mastery(30, (e1,)))
    mastery.save(_mastery(40, (e1, e2)))  # extends history: allowed
    stored = mastery.get("u1", "c1")
    assert stored is not None
    assert stored.events == (e1, e2)

    # Dropping or rewriting prior events is rejected (append-only, Req 5.7).
    with pytest.raises(ValueError, match="append-only"):
        mastery.save(_mastery(40, (e1,)))
    with pytest.raises(ValueError, match="append-only"):
        mastery.save(_mastery(40, (MasteryEvent(delta=99, source="x", occurred_at=_T0), e2)))


def test_mastery_hint_reveals_are_append_only_and_listed_per_user() -> None:
    mastery = In_Memory_MasteryRepository()
    mastery.add_hint_reveal(HintRevealRecord(user_id="u1", question_id="qn1", revealed_at=_T0))
    mastery.add_hint_reveal(HintRevealRecord(user_id="u1", question_id="qn2", revealed_at=_T0))
    mastery.add_hint_reveal(HintRevealRecord(user_id="u2", question_id="qn1", revealed_at=_T0))
    assert len(mastery.list_hint_reveals("u1")) == 2
    assert len(mastery.list_hint_reveals("u2")) == 1


# --- Review (Req 9.1, 9.2, 9.4, 9.5) -----------------------------------------


def test_review_satisfies_its_port() -> None:
    reviews: ReviewRepository = In_Memory_ReviewRepository()
    assert isinstance(reviews, ReviewRepository)


def _schedule(
    due_at_offset: timedelta,
    history: tuple[ReviewAttempt, ...],
) -> ReviewSchedule:
    return ReviewSchedule(
        user_id="u1",
        concept_id="c1",
        due_at=_T0 + due_at_offset,
        interval=timedelta(days=1),
        history=history,
    )


def test_review_resumes_single_entry_and_keeps_history_append_only() -> None:
    reviews = In_Memory_ReviewRepository()
    assert reviews.get("u1", "c1") is None

    a1 = ReviewAttempt(passed=True, attempted_at=_T0)
    reviews.save(_schedule(timedelta(days=1), ()))
    reviews.save(_schedule(timedelta(days=3), (a1,)))  # resume same entry, append attempt

    # Still exactly one entry for (u1, c1) — resumed, not duplicated (Req 9.2).
    assert len(reviews.list_for_user("u1")) == 1
    stored = reviews.get("u1", "c1")
    assert stored is not None
    assert stored.history == (a1,)
    assert stored.due_at == _T0 + timedelta(days=3)

    # Dropping a prior attempt is rejected (append-only, Req 9.4, 9.5).
    with pytest.raises(ValueError, match="append-only"):
        reviews.save(_schedule(timedelta(days=5), ()))


# --- Attempts (Req 3.1, 3.6, 6.1, 10.4, 10.5) --------------------------------


def test_attempts_satisfies_its_port() -> None:
    attempts: AttemptRepository = In_Memory_AttemptRepository()
    assert isinstance(attempts, AttemptRepository)


def _attempt(stable_id: str, score: int, offset: timedelta) -> QuizAttempt:
    return QuizAttempt(
        stable_id=stable_id,
        quiz_id="q1",
        score=score,
        is_best=False,
        attempted_at=_T0 + offset,
    )


def test_attempts_reject_duplicate_stable_id_and_list_oldest_first() -> None:
    attempts = In_Memory_AttemptRepository()
    answer = Answer(question_id="qn1", first_answered_at=_T0, counted_for_mastery=True)
    attempts.add_quiz_attempt("u1", _attempt("a1", 50, timedelta()), (answer,))
    attempts.add_quiz_attempt("u1", _attempt("a2", 80, timedelta(hours=1)), ())

    with pytest.raises(ValueError, match="stable_id"):
        attempts.add_quiz_attempt("u1", _attempt("a1", 10, timedelta(hours=2)), ())

    listed = attempts.list_quiz_attempts("u1", "q1")
    assert tuple(a.stable_id for a in listed) == ("a1", "a2")
    assert attempts.get_quiz_attempt("a2").score == 80  # type: ignore[union-attr]

    answers = attempts.list_answers_for_question("u1", "qn1")
    assert len(answers) == 1 and answers[0].first_answered_at == _T0


def test_attempts_isolate_answers_and_attempts_per_user() -> None:
    attempts = In_Memory_AttemptRepository()
    attempts.add_quiz_attempt("u1", _attempt("a1", 50, timedelta()), ())
    attempts.add_quiz_attempt("u2", _attempt("a2", 90, timedelta(hours=1)), ())

    assert tuple(a.stable_id for a in attempts.list_quiz_attempts("u1", "q1")) == ("a1",)
    assert tuple(a.stable_id for a in attempts.list_quiz_attempts("u2", "q1")) == ("a2",)


def test_attempts_project_submission_pending_then_reviewed() -> None:
    attempts = In_Memory_AttemptRepository()
    pending = ProjectSubmissionRef(stable_id="s1", project_id="p1")
    attempts.add_project_submission("u1", pending)

    with pytest.raises(ValueError, match="stable_id"):
        attempts.add_project_submission("u1", ProjectSubmissionRef(stable_id="s1", project_id="p9"))

    # Pending: no outcome recorded yet (Req 10.4, 10.5).
    assert attempts.get_project_submission("s1").review_outcome is None  # type: ignore[union-attr]

    # Reviewer completes: the outcome is written in place, keyed by stable_id.
    attempts.save_project_submission(
        ProjectSubmissionRef(stable_id="s1", project_id="p1", review_outcome="passed")
    )
    assert attempts.get_project_submission("s1").review_outcome == "passed"  # type: ignore[union-attr]
    assert {s.stable_id for s in attempts.list_project_submissions("u1")} == {"s1"}

    # Saving an unknown submission is rejected.
    with pytest.raises(ValueError, match="does not exist"):
        attempts.save_project_submission(ProjectSubmissionRef(stable_id="missing", project_id="p1"))
