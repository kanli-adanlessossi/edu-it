"""Tests for the PostgreSQL learning repositories (task 20.1, Req 17.2, 17.4, 17.5, 18.1).

The adapters are database-agnostic SQLAlchemy Core, so they run here against an
in-memory SQLite database created from the shared data-model ``metadata`` (foreign
keys enforced), exactly as the identity repository tests do. Coverage: port
conformance, stable_id-only addressing, catalog reads, the database-enforced
invariants (one enrollment per learner x track, one schedule per learner x concept,
unique Idempotency-Key), append-only history, and keyset pagination.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

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
)
from edu_it_domain.ports.assessments import (
    AssessmentRecord,
    AssessmentRepository,
    AssessmentStatus,
)
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.enrollment import EnrollmentRecord, EnrollmentRepository
from edu_it_domain.ports.lessons import LessonRepository
from edu_it_domain.ports.mastery import HintRevealRecord, MasteryRepository
from edu_it_domain.ports.progress import (
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)
from edu_it_domain.ports.review import ReviewRepository
from edu_it_infrastructure.persistence import (
    DuplicateQuizAttemptError,
    Postgres_AssessmentRepository,
    Postgres_AttemptRepository,
    Postgres_CatalogRepository,
    Postgres_EnrollmentRepository,
    Postgres_LessonRepository,
    Postgres_MasteryRepository,
    Postgres_ProgressRepository,
    Postgres_ReviewRepository,
    metadata,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_T = metadata.tables


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection: Any, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(eng)
    _seed(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed(eng: Engine) -> None:
    """Seed one user x two, one track, two modules, a lesson, a quiz, a rubric, concepts."""
    with eng.begin() as c:
        for sid in ("u1", "u2"):
            c.execute(
                insert(_T["user"]).values(
                    stable_id=sid, email=f"{sid}@e.test", locale="en", status="verified"
                )
            )
        c.execute(
            insert(_T["content_provenance"]).values(
                stable_id="prov-1", content_version="v1", origin="ai"
            )
        )
        c.execute(
            insert(_T["track"]).values(
                stable_id="t1", provenance_id=1, title_fr="T", title_en="T", status="active"
            )
        )
        c.execute(
            insert(_T["module"]),
            [
                {
                    "stable_id": "m2",
                    "track_id": 1,
                    "provenance_id": 1,
                    "ordinal": 2,
                    "prerequisite_concept_ids": json.dumps(["c1"]),
                },
                {
                    "stable_id": "m1",
                    "track_id": 1,
                    "provenance_id": 1,
                    "ordinal": 1,
                    "prerequisite_concept_ids": None,
                },
            ],
        )
        c.execute(
            insert(_T["lesson"]),
            [
                {"stable_id": "l1", "module_id": 2, "provenance_id": 1, "ordinal": 1},
                {"stable_id": "l2", "module_id": 2, "provenance_id": 1, "ordinal": 2},
            ],
        )
        c.execute(insert(_T["quiz"]).values(stable_id="q1", lesson_id=1, provenance_id=1))
        c.execute(
            insert(_T["question"]),
            [
                {
                    "stable_id": "qs2",
                    "quiz_id": 1,
                    "provenance_id": 1,
                    "prompt_text": "p",
                    "concept_stable_ids": json.dumps(["c1", "c2"]),
                },
                {
                    "stable_id": "qs1",
                    "quiz_id": 1,
                    "provenance_id": 1,
                    "prompt_text": "p",
                    "concept_stable_ids": json.dumps(["c1"]),
                },
            ],
        )
        c.execute(
            insert(_T["question_option"]),
            [
                {"question_id": 1, "is_correct": True},
                {"question_id": 1, "is_correct": False},
                {"question_id": 2, "is_correct": True},
            ],
        )
        for sid in ("c1", "c2", "c3"):
            c.execute(
                insert(_T["concept"]).values(
                    stable_id=sid, provenance_id=1, title_fr=sid, title_en=sid
                )
            )
        c.execute(insert(_T["prerequisite"]).values(concept_id=2, requires_concept_id=1))
        c.execute(insert(_T["prerequisite"]).values(concept_id=2, requires_concept_id=3))
        c.execute(insert(_T["project"]).values(stable_id="p1", provenance_id=1))
        c.execute(insert(_T["rubric"]).values(stable_id="r1", project_id=1))
        c.execute(
            insert(_T["rubric_criterion"]),
            [{"rubric_id": 1, "max_points": 5}, {"rubric_id": 1, "max_points": 10}],
        )


# --- Port conformance ---------------------------------------------------------


def test_adapters_satisfy_their_ports(engine: Engine) -> None:
    assert isinstance(Postgres_CatalogRepository(engine), CatalogRepository)
    assert isinstance(Postgres_EnrollmentRepository(engine), EnrollmentRepository)
    assert isinstance(Postgres_ProgressRepository(engine), ProgressRepository)
    assert isinstance(Postgres_MasteryRepository(engine), MasteryRepository)
    assert isinstance(Postgres_ReviewRepository(engine), ReviewRepository)
    assert isinstance(Postgres_AttemptRepository(engine), AttemptRepository)
    assert isinstance(Postgres_LessonRepository(engine), LessonRepository)
    assert isinstance(Postgres_AssessmentRepository(engine), AssessmentRepository)


# --- Catalog ------------------------------------------------------------------


def test_catalog_modules_by_stable_id_and_track_order(engine: Engine) -> None:
    catalog = Postgres_CatalogRepository(engine)
    module = catalog.get_module("m2")
    assert module is not None
    assert (module.track_id, module.ordinal, module.prerequisite_concept_ids) == ("t1", 2, ("c1",))
    assert catalog.get_module("nope") is None
    assert [x.stable_id for x in catalog.list_modules_for_track("t1")] == ["m1", "m2"]
    page = catalog.list_modules_page("t1", after=(1, "m1"), limit=5)
    assert [x.stable_id for x in page] == ["m2"]


def test_catalog_concept_edges_quiz_and_pool(engine: Engine) -> None:
    catalog = Postgres_CatalogRepository(engine)
    concept = catalog.get_concept("c2")
    assert concept is not None and concept.requires == ("c1", "c3")
    assert catalog.get_concept("zzz") is None

    quiz = catalog.get_quiz("q1")
    assert quiz is not None
    assert (quiz.lesson_id, quiz.content_version) == ("l1", "v1")
    assert [q.stable_id for q in quiz.questions] == ["qs2", "qs1"]
    assert quiz.questions[0].options == (
        QuestionOption(option_id=1, is_correct=True),
        QuestionOption(option_id=2, is_correct=False),
    )
    assert quiz.questions[0].concept_ids == ("c1", "c2")
    assert catalog.get_quiz_at_version("q1", "v1") == quiz
    assert catalog.get_quiz_at_version("q1", "old") is None

    assert [q.stable_id for q in catalog.list_questions_for_concept("c1")] == ["qs1", "qs2"]
    assert [q.stable_id for q in catalog.list_questions_for_concept("c2")] == ["qs2"]
    assert catalog.list_questions_for_concept("c3") == ()


def test_catalog_rubric(engine: Engine) -> None:
    rubric = Postgres_CatalogRepository(engine).get_rubric_for_project("p1")
    assert rubric is not None
    assert rubric.stable_id == "r1"
    assert [(c.stable_id, c.max_points) for c in rubric.criteria] == [("r1#1", 5), ("r1#2", 10)]
    assert Postgres_CatalogRepository(engine).get_rubric_for_project("none") is None


# --- Enrollment ---------------------------------------------------------------


def _enrollment(stable_id: str = "e1", user: str = "u1", at: datetime = _T0) -> EnrollmentRecord:
    return EnrollmentRecord(stable_id=stable_id, user_id=user, track_id="t1", enrolled_at=at)


def test_enrollment_roundtrip_and_duplicates(engine: Engine) -> None:
    repo = Postgres_EnrollmentRepository(engine)
    repo.add(_enrollment())
    assert repo.get("e1") == _enrollment()
    assert repo.get_for_user_and_track("u1", "t1") == _enrollment()
    assert repo.get("missing") is None
    with pytest.raises(ValueError):
        repo.add(_enrollment("e1", user="u2"))  # reused stable_id
    with pytest.raises(ValueError):
        repo.add(_enrollment("e2"))  # same learner x track
    with pytest.raises(ValueError):
        repo.add(_enrollment("e3", user="ghost"))  # unknown learner


def test_enrollment_keyset_page_covers_all_once(engine: Engine) -> None:
    repo = Postgres_EnrollmentRepository(engine)
    with engine.begin() as c:
        for i in range(2, 6):
            c.execute(
                insert(_T["track"]).values(
                    stable_id=f"t{i}", provenance_id=1, title_fr="T", title_en="T", status="a"
                )
            )
    for i in range(1, 6):
        e = EnrollmentRecord(f"e{i}", "u1", f"t{i}", _T0)  # identical instants: id tie-break
        repo.add(e)
    seen: list[str] = []
    after = None
    while True:
        page = repo.list_for_user_page("u1", after=after, limit=2)
        if not page:
            break
        seen.extend(e.stable_id for e in page)
        after = (page[-1].enrolled_at, page[-1].stable_id)
    assert seen == ["e1", "e2", "e3", "e4", "e5"]
    assert [e.stable_id for e in repo.list_for_user("u1")] == seen
    with pytest.raises(ValueError):
        repo.list_for_user_page("u1", limit=0)


# --- Progress -----------------------------------------------------------------


def test_progress_upsert_and_scoped_completions(engine: Engine) -> None:
    enrollments = Postgres_EnrollmentRepository(engine)
    enrollments.add(_enrollment("e1", "u1"))
    progress = Postgres_ProgressRepository(engine)

    record = ModuleProgressRecord("e1", "m2", "unlocked")
    progress.upsert_module_progress(record)
    progress.upsert_module_progress(ModuleProgressRecord("e1", "m2", "completed", tested_out=True))
    assert progress.get_module_progress("e1", "m2") == ModuleProgressRecord(
        "e1", "m2", "completed", True
    )
    assert len(progress.list_module_progress("e1")) == 1
    with pytest.raises(ValueError):
        progress.upsert_module_progress(ModuleProgressRecord("nope", "m2", "locked"))

    first = LessonCompletionRecord("m2", "l1", _T0)
    progress.add_lesson_completion(first)
    progress.add_lesson_completion(LessonCompletionRecord("m2", "l1", _T0 + timedelta(days=3)))
    assert progress.list_lesson_completions("m2") == (first,)  # idempotent, earliest kept
    assert progress.list_lesson_completions("m1") == ()
    with pytest.raises(ValueError):  # no progress row for m1
        progress.add_lesson_completion(LessonCompletionRecord("m1", "l1", _T0))


def test_progress_completions_never_cross_learners(engine: Engine) -> None:
    enrollments = Postgres_EnrollmentRepository(engine)
    enrollments.add(_enrollment("e1", "u1"))
    enrollments.add(_enrollment("e2", "u2"))
    progress = Postgres_ProgressRepository(engine)
    progress.upsert_module_progress(ModuleProgressRecord("e1", "m2", "in_progress"))
    progress.upsert_module_progress(ModuleProgressRecord("e2", "m2", "in_progress"))

    with pytest.raises(ValueError):  # ambiguous when unbound
        progress.add_lesson_completion(LessonCompletionRecord("m2", "l1", _T0))
    one = progress.for_enrollment("e1")
    one.add_lesson_completion(LessonCompletionRecord("m2", "l1", _T0))
    assert len(one.list_lesson_completions("m2")) == 1
    assert progress.for_enrollment("e2").list_lesson_completions("m2") == ()


# --- Mastery ------------------------------------------------------------------


def _event(delta: int, minutes: int) -> MasteryEvent:
    return MasteryEvent(delta=delta, source="quiz", occurred_at=_T0 + timedelta(minutes=minutes))


def test_mastery_save_is_append_only(engine: Engine) -> None:
    repo = Postgres_MasteryRepository(engine)
    assert repo.get("u1", "c1") is None
    first = Mastery("u1", "c1", 10, MasteryState.INTRODUCED, (_event(10, 0),))
    repo.save(first)
    assert repo.get("u1", "c1") == first

    grown = Mastery("u1", "c1", 40, MasteryState.PRACTICED, (*first.events, _event(30, 5)))
    repo.save(grown)
    assert repo.get("u1", "c1") == grown
    assert repo.list_for_user("u1") == (grown,)
    assert repo.list_for_user("u2") == ()

    with pytest.raises(ValueError):
        repo.save(Mastery("u1", "c1", 0, MasteryState.INTRODUCED, ()))  # drops history
    with pytest.raises(ValueError):
        repo.save(Mastery("u1", "c1", 0, MasteryState.INTRODUCED, (_event(1, 0),)))  # rewrites
    assert repo.get("u1", "c1") == grown


def test_hint_reveals_are_recorded_oldest_first(engine: Engine) -> None:
    repo = Postgres_MasteryRepository(engine)
    later = HintRevealRecord("u1", "qs1", _T0 + timedelta(hours=1))
    earlier = HintRevealRecord("u1", "qs2", _T0)
    repo.add_hint_reveal(later)
    repo.add_hint_reveal(earlier)
    assert repo.list_hint_reveals("u1") == (earlier, later)
    assert repo.list_hint_reveals("u2") == ()
    with pytest.raises(ValueError):
        repo.add_hint_reveal(HintRevealRecord("u1", "ghost", _T0))


# --- Review -------------------------------------------------------------------


def test_review_schedule_single_entry_resumes_and_appends(engine: Engine) -> None:
    repo = Postgres_ReviewRepository(engine)
    assert repo.get("u1", "c1") is None
    created = ReviewSchedule("u1", "c1", _T0 + timedelta(days=1), timedelta(days=1))
    repo.save(created)
    assert repo.get("u1", "c1") == created

    attempt = ReviewAttempt(passed=True, attempted_at=_T0 + timedelta(days=1))
    resumed = ReviewSchedule(
        "u1", "c1", _T0 + timedelta(days=3), timedelta(days=2), history=(attempt,)
    )
    repo.save(resumed)
    assert repo.get("u1", "c1") == resumed
    assert repo.list_for_user("u1") == (resumed,)  # still one entry

    with pytest.raises(ValueError):
        repo.save(ReviewSchedule("u1", "c1", _T0, timedelta(days=1), history=()))


# --- Attempts and submissions ---------------------------------------------------


def _attempt(stable_id: str, minutes: int, score: int = 80) -> QuizAttempt:
    return QuizAttempt(
        stable_id=stable_id,
        quiz_id="q1",
        score=score,
        is_best=False,
        attempted_at=_T0 + timedelta(minutes=minutes),
        content_version="v1",
    )


def test_quiz_attempt_roundtrip_answers_and_idempotency_key(engine: Engine) -> None:
    repo = Postgres_AttemptRepository(engine)
    answers = (Answer("qs1", _T0, True), Answer("qs2", _T0, False))
    repo.add_quiz_attempt("u1", _attempt("a1", 0), answers, idempotency_key="key-1")

    assert repo.get_quiz_attempt("a1") == _attempt("a1", 0)
    assert repo.get_quiz_attempt_by_idempotency_key("key-1") == _attempt("a1", 0)
    assert repo.get_quiz_attempt_by_idempotency_key("other") is None
    assert repo.list_answers_for_question("u1", "qs1") == (answers[0],)
    assert repo.list_answers_for_question("u2", "qs1") == ()

    with pytest.raises(DuplicateQuizAttemptError) as same_key:
        repo.add_quiz_attempt("u1", _attempt("a2", 1), (), idempotency_key="key-1")
    assert same_key.value.idempotency_key_conflict is True
    with pytest.raises(DuplicateQuizAttemptError) as same_id:
        repo.add_quiz_attempt("u1", _attempt("a1", 1), ())
    assert same_id.value.idempotency_key_conflict is False
    assert repo.list_quiz_attempts("u1", "q1") == (_attempt("a1", 0),)  # exactly one stored


def test_failed_attempt_write_leaves_nothing_behind(engine: Engine) -> None:
    repo = Postgres_AttemptRepository(engine)
    with pytest.raises(ValueError):
        repo.add_quiz_attempt("u1", _attempt("a1", 0), (Answer("ghost", _T0, True),))
    assert repo.get_quiz_attempt("a1") is None


def test_quiz_attempts_are_listed_oldest_first_and_paged(engine: Engine) -> None:
    repo = Postgres_AttemptRepository(engine)
    repo.add_quiz_attempt("u1", _attempt("a2", 5), ())
    repo.add_quiz_attempt("u1", _attempt("a1", 0), ())
    repo.add_quiz_attempt("u1", _attempt("a3", 5), ())  # same instant: stable_id tie-break
    repo.add_quiz_attempt("u2", _attempt("b1", 1), ())
    assert [a.stable_id for a in repo.list_quiz_attempts("u1", "q1")] == ["a1", "a2", "a3"]
    first = repo.list_quiz_attempts_page("u1", "q1", limit=2)
    rest = repo.list_quiz_attempts_page(
        "u1", "q1", after=(first[-1].attempted_at, first[-1].stable_id), limit=2
    )
    assert [a.stable_id for a in (*first, *rest)] == ["a1", "a2", "a3"]


def test_project_submission_pending_then_reviewed(engine: Engine) -> None:
    repo = Postgres_AttemptRepository(engine)
    pending = ProjectSubmissionRef("s1", "p1")
    repo.add_project_submission("u1", pending)
    assert repo.get_project_submission("s1") == pending
    with pytest.raises(ValueError):
        repo.add_project_submission("u1", pending)

    reviewed = ProjectSubmissionRef("s1", "p1", review_outcome="passed")
    repo.save_project_submission(reviewed)
    assert repo.get_project_submission("s1") == reviewed
    assert repo.list_project_submissions("u1") == (reviewed,)
    assert repo.list_project_submissions("u2") == ()
    with pytest.raises(ValueError):
        repo.save_project_submission(ProjectSubmissionRef("nope", "p1"))


# --- Property: keyset pagination covers the set exactly once -----------------------


@settings(max_examples=100, deadline=None)
@given(
    minutes=st.lists(st.integers(min_value=0, max_value=5), min_size=0, max_size=12),
    limit=st.integers(min_value=1, max_value=5),
)
def test_keyset_pages_cover_every_attempt_exactly_once(minutes: list[int], limit: int) -> None:
    """Feature: learning-core, Property 28: Cursor pagination covers the full set exactly once.

    **Validates: Requirements 17.5**
    """
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    try:
        metadata.create_all(eng)
        _seed(eng)
        repo = Postgres_AttemptRepository(eng)
        for index, offset in enumerate(minutes):
            repo.add_quiz_attempt("u1", _attempt(f"a{index:02d}", offset), ())
        seen: list[str] = []
        after = None
        while True:
            page = repo.list_quiz_attempts_page("u1", "q1", after=after, limit=limit)
            if not page:
                break
            seen.extend(a.stable_id for a in page)
            after = (page[-1].attempted_at, page[-1].stable_id)
        expected = sorted(
            (f"a{i:02d}" for i in range(len(minutes))), key=lambda s: (minutes[int(s[1:])], s)
        )
        assert seen == expected
    finally:
        eng.dispose()


# --- Lessons and assessments --------------------------------------------------


def test_lesson_structure_from_rows_and_text_from_source(engine: Engine) -> None:
    lessons = Postgres_LessonRepository(engine, lambda sid: ({"en": f"T {sid}"}, {"en": "# body"}))
    lesson = lessons.get_lesson("l1")
    assert lesson is not None
    assert (lesson.module_id, lesson.quiz_id, lesson.content_version) == ("m1", "q1", "v1")
    assert dict(lesson.bodies) == {"en": "# body"}
    assert Postgres_LessonRepository(engine).get_lesson("l2").quiz_id is None  # type: ignore[union-attr]
    assert lessons.get_lesson("nope") is None


def test_assessments_upsert_per_learner_and_track(engine: Engine) -> None:
    repo = Postgres_AssessmentRepository(engine)
    assert repo.get_placement("u1", "t1") is None
    repo.save_placement(AssessmentRecord("u1", "t1", AssessmentStatus.PENDING))
    repo.save_placement(AssessmentRecord("u1", "t1", AssessmentStatus.COMPLETED))
    placement = repo.get_placement("u1", "t1")
    assert placement is not None and placement.status is AssessmentStatus.COMPLETED
    assert repo.get_final_assessment("u1", "t1") is None
    repo.save_final_assessment(AssessmentRecord("u1", "t1", AssessmentStatus.IN_PROGRESS))
    final = repo.get_final_assessment("u1", "t1")
    assert final is not None and final.status is AssessmentStatus.IN_PROGRESS
    with pytest.raises(ValueError):
        repo.save_placement(AssessmentRecord("ghost", "t1", AssessmentStatus.PENDING))
