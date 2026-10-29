"""End-to-end learning integration suite (task 23.1).

Drives the real application use cases (``edu_it_application.learning`` and
``edu_it_application.gdpr``) over the real SQL repositories (SQLAlchemy Core on
in-memory SQLite, created from the shared data-model ``metadata``), the Redis-backed
idempotency and session adapters (over ``fakeredis``, shared by every "worker"), and
the injected ``Test_Clock``. Numbers come from ``edu_it_domain.config``; nothing here
hardcodes a business value.

Covers:

- the full journey: session -> enrollment -> progress -> lesson -> hint -> quiz attempt
  (Redis idempotent replay) -> Clock-driven review lifecycle (due, passed, failed with
  the expected interval changes) -> project submission (pending, then reviewed);
- content-version pinning of an in-flight attempt, and deferred archiving of a removed
  item while a session is active;
- GDPR export and deletion across *every* personal-data table.

**Validates: Requirements 25.1, 25.2, 25.3, 26.2, 26.3, 9.4, 9.5, 9.6, 22.1, 22.2, 22.3,
30.6, 30.8**
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_application.learning import (
    AttemptQuizRequest,
    AttemptQuizResult,
    AttemptQuizUseCase,
    EnrollUseCase,
    GetLessonUseCase,
    GetProgressUseCase,
    RevealHintUseCase,
    ReviewNotDueError,
    StartReviewUseCase,
    SubmissionStatus,
    SubmitProjectRequest,
    SubmitProjectUseCase,
    SubmitReviewRequest,
    SubmitReviewUseCase,
)
from edu_it_application.learning.attempt_codec import (
    decode_attempt_result,
    encode_attempt_result,
)
from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import load_content
from edu_it_domain.config import (
    COOLDOWN_INTERVAL,
    DECAY_RATE_PER_DAY,
    FAILED_REVIEW_INTERVAL,
    INITIAL_REVIEW_INTERVAL,
    MASTERY_GAIN_PER_CORRECT_ANSWER,
    MASTERY_THRESHOLDS,
    MIN_ELIGIBLE_QUESTION_COUNT,
    PASSED_REVIEW_INTERVAL,
    REVIEW_PASSED_MASTERY_GAIN,
    SESSION_DURATION,
)
from edu_it_domain.learning.hints import tempered_gain
from edu_it_domain.learning.models import MasteryState
from edu_it_domain.learning.review import is_due, is_overdue, overdue_duration
from edu_it_domain.ports.assessments import AssessmentRecord, AssessmentStatus
from edu_it_domain.ports.catalog import QuizRecord
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_domain.ports.progress import (
    MODULE_STATE_LOCKED,
    MODULE_STATE_UNLOCKED,
    LessonCompletionRecord,
)
from edu_it_domain.ports.repository import SessionRecord
from edu_it_infrastructure.adapters import Redis_SessionStore
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.fakes import (
    Fake_ProjectReviewer,
    In_Memory_CatalogRepository,
    In_Memory_ObjectStorage,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_AssessmentRepository,
    Postgres_AttemptRepository,
    Postgres_CatalogRepository,
    Postgres_EnrollmentRepository,
    Postgres_LessonRepository,
    Postgres_MasteryRepository,
    Postgres_PersonalDataGateway,
    Postgres_ProgressRepository,
    Postgres_ReviewRepository,
    Postgres_SessionRepository,
    metadata,
)
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_T = metadata.tables

_U1, _U2 = "usr-lea", "usr-sam"
_TRACK = "trk-linux"
_CONCEPT = "con-perms"
_QUIZ = "qz-perms"
_QUESTIONS = ("qn-1", "qn-2", "qn-3")
_PROJECT = "prj-harden"
_CRITERIA = ("rub-1#1", "rub-1#2")


# --- Fixtures: real schema, seeded catalog, Redis-backed adapters ----------------


def _seed(engine: Engine) -> None:
    """Seed two learners and a small catalog: 2 modules, 2 lessons, a quiz, a project."""
    with engine.begin() as c:
        for sid in (_U1, _U2):
            c.execute(
                insert(_T["user"]).values(
                    stable_id=sid, email=f"{sid}@e.test", locale="en", status="verified"
                )
            )
        c.execute(
            insert(_T["content_provenance"]),
            [
                {"stable_id": "prov-1", "content_version": "v1", "origin": "ai"},
                {"stable_id": "prov-quiz", "content_version": "v1", "origin": "ai"},
            ],
        )
        c.execute(
            insert(_T["track"]).values(
                stable_id=_TRACK, provenance_id=1, title_fr="T", title_en="T", status="active"
            )
        )
        c.execute(
            insert(_T["module"]),
            [
                {
                    "stable_id": "mod-1",
                    "track_id": 1,
                    "provenance_id": 1,
                    "ordinal": 1,
                    "prerequisite_concept_ids": None,
                },
                {
                    "stable_id": "mod-2",
                    "track_id": 1,
                    "provenance_id": 1,
                    "ordinal": 2,
                    "prerequisite_concept_ids": json.dumps([_CONCEPT]),
                },
            ],
        )
        c.execute(
            insert(_T["lesson"]),
            [
                {"stable_id": "les-1", "module_id": 1, "provenance_id": 1, "ordinal": 1},
                {"stable_id": "les-2", "module_id": 1, "provenance_id": 1, "ordinal": 2},
            ],
        )
        c.execute(insert(_T["quiz"]).values(stable_id=_QUIZ, lesson_id=1, provenance_id=2))
        c.execute(
            insert(_T["question"]),
            [
                {
                    "stable_id": q,
                    "quiz_id": 1,
                    "provenance_id": 2,
                    "prompt_text": "p",
                    "concept_stable_ids": json.dumps([_CONCEPT]),
                }
                for q in _QUESTIONS
            ],
        )
        c.execute(
            insert(_T["question_option"]),
            [
                {"question_id": qid, "is_correct": correct}
                for qid in (1, 2, 3)
                for correct in (True, False)
            ],
        )
        c.execute(
            insert(_T["concept"]).values(
                stable_id=_CONCEPT, provenance_id=1, title_fr="c", title_en="c"
            )
        )
        c.execute(insert(_T["project"]).values(stable_id=_PROJECT, provenance_id=1))
        c.execute(insert(_T["rubric"]).values(stable_id="rub-1", project_id=1))
        c.execute(
            insert(_T["rubric_criterion"]),
            [{"rubric_id": 1, "max_points": 10}, {"rubric_id": 1, "max_points": 10}],
        )
        c.execute(insert(_T["lab"]).values(stable_id="lab-1", lesson_id=1, provenance_id=1))
        c.execute(insert(_T["lab_task"]).values(stable_id="lt-1", lab_id=1, ordinal=1))


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


_LESSON_TEXT: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "les-1": ({"en": "Reading permissions"}, {"en": "# Reading permissions"}),
    "les-2": (
        {"fr": "Changer les droits", "en": "Changing permissions"},
        {"fr": "# Changer les droits", "en": "# Changing permissions"},
    ),
}


class _World:
    """Every port wired as the composition root does: SQL repos + Redis adapters + Test_Clock."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.clock = Test_Clock(start=_T0)
        self.ids = Sequential_IdGenerator("id")
        self.redis = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        self.idempotency = Redis_IdempotencyStore(
            self.redis, encode=encode_attempt_result, decode=decode_attempt_result
        )
        self.session_store = Redis_SessionStore(self.redis, self.clock)
        self.durable_sessions = Postgres_SessionRepository(engine)
        self.catalog = Postgres_CatalogRepository(engine)
        self.enrollments = Postgres_EnrollmentRepository(engine)
        self.progress = Postgres_ProgressRepository(engine)
        self.mastery = Postgres_MasteryRepository(engine)
        self.reviews = Postgres_ReviewRepository(engine)
        self.attempts = Postgres_AttemptRepository(engine)
        self.lessons = Postgres_LessonRepository(engine, lambda sid: _LESSON_TEXT[sid])
        self.assessments = Postgres_AssessmentRepository(engine)
        self.quiz = self._load_quiz()

    def _load_quiz(self) -> QuizRecord:
        quiz = self.catalog.get_quiz(_QUIZ)
        assert quiz is not None
        return quiz

    # -- option helpers ----------------------------------------------------------

    def selections(self, *, correct: bool, quiz: QuizRecord | None = None) -> dict[str, int | None]:
        """Pick, for every question, the correct (or a wrong) option id."""
        source = quiz or self.quiz
        return {
            q.stable_id: next(o.option_id for o in q.options if o.is_correct is correct)
            for q in source.questions
        }

    # -- use cases ---------------------------------------------------------------

    def enroll(self, user: str) -> str:
        result = EnrollUseCase(
            catalog=self.catalog,
            enrollments=self.enrollments,
            progress=self.progress,
            mastery=self.mastery,
            clock=self.clock,
            ids=self.ids,
        ).execute(user, _TRACK)
        return result.enrollment.stable_id

    def get_progress(self, user: str, enrollment_id: str) -> Any:
        return GetProgressUseCase(
            catalog=self.catalog,
            enrollments=self.enrollments,
            progress=self.progress.for_enrollment(enrollment_id),
            mastery=self.mastery,
        ).execute(user, _TRACK)

    def reveal_hint(self, user: str, question: str) -> Any:
        return RevealHintUseCase(
            catalog=self.catalog, mastery=self.mastery, clock=self.clock
        ).execute(user, _QUIZ, question)

    def attempt_quiz(
        self,
        user: str,
        selections: dict[str, int | None],
        key: str,
        *,
        catalog: Any = None,
        content_version: str | None = None,
    ) -> AttemptQuizResult:
        return AttemptQuizUseCase(
            catalog=catalog or self.catalog,
            attempts=self.attempts,
            mastery=self.mastery,
            reviews=self.reviews,
            idempotency=self.idempotency,
            clock=self.clock,
            ids=self.ids,
        ).execute(
            AttemptQuizRequest(
                user_id=user,
                quiz_id=_QUIZ,
                selections=selections,
                idempotency_key=key,
                content_version=content_version,
            )
        )

    def start_review(self, user: str) -> Any:
        return StartReviewUseCase(
            catalog=self.catalog, reviews=self.reviews, clock=self.clock
        ).execute(user, _CONCEPT)

    def submit_review(self, user: str, selections: dict[str, int | None]) -> Any:
        return SubmitReviewUseCase(
            catalog=self.catalog, reviews=self.reviews, mastery=self.mastery, clock=self.clock
        ).execute(SubmitReviewRequest(user, _CONCEPT, selections))

    def submit_project(self, user: str, *, pending: bool) -> Any:
        return SubmitProjectUseCase(
            catalog=self.catalog,
            attempts=self.attempts,
            reviewer=Fake_ProjectReviewer(default_points=10, pending=pending),
            ids=self.ids,
        ).execute(
            SubmitProjectRequest(
                user_id=user,
                project_id=_PROJECT,
                self_assessment={_CRITERIA[0]: 8, _CRITERIA[1]: 9},
                evidence=["https://example.test/evidence"],
            )
        )

    def review_selections(self, user: str, *, correct: bool) -> dict[str, int | None]:
        questions = self.start_review(user)
        return {
            q.stable_id: next(o.option_id for o in q.options if o.is_correct is correct)
            for q in questions
        }


# --- 1. End-to-end journey (Req 9.4, 9.5, 9.6, 30.6, 30.8) -------------------------


def test_enrollment_to_project_submission_journey(engine: Engine) -> None:
    w = _World(engine)
    assert len(w.quiz.questions) >= MIN_ELIGIBLE_QUESTION_COUNT

    # Session (Redis) establishes who the learner is; a durable row backs it (ADR 0004).
    expires = _T0 + SESSION_DURATION
    w.session_store.create("ref-lea", _U1, expires_at=expires)
    w.durable_sessions.add(SessionRecord("ref-lea", _U1, expires))
    resolved = w.session_store.resolve("ref-lea")
    assert resolved is not None
    user = resolved.user_stable_id
    assert user == _U1

    # Enrollment seeds module lock states.
    enrollment_id = w.enroll(user)
    before = w.get_progress(user, enrollment_id)
    assert [m.state for m in before.modules] == [MODULE_STATE_UNLOCKED, MODULE_STATE_LOCKED]
    assert w.enroll(user) == enrollment_id  # enrolling again is idempotent

    # Lesson: served in the requested locale, English fallback otherwise (Req 17.3).
    get_lesson = GetLessonUseCase(lessons=w.lessons)
    assert get_lesson.execute("les-1", "fr").locale == "en"
    assert get_lesson.execute("les-2", "fr").locale == "fr"
    lesson = get_lesson.execute("les-1", "en")
    assert (lesson.module_id, lesson.quiz_id, lesson.content_version) == ("mod-1", _QUIZ, "v1")
    w.progress.for_enrollment(enrollment_id).add_lesson_completion(
        LessonCompletionRecord("mod-1", "les-1", w.clock.now())
    )

    # Hint: one reveal and one mastery event for the question's concept (Req 7.1).
    hint = w.reveal_hint(user, _QUESTIONS[0])
    assert hint.hint_level == 1
    assert len(w.mastery.list_hint_reveals(user)) == 1

    # Quiz attempt through the Redis idempotency store: replay records nothing more.
    right = w.selections(correct=True)
    first = w.attempt_quiz(user, right, "key-1")
    replay = w.attempt_quiz(user, right, "key-1")
    assert not first.replayed and replay.replayed
    assert replay.attempt == first.attempt
    assert len(w.attempts.list_quiz_attempts(user, _QUIZ)) == 1
    assert first.attempt.score == 100 and first.attempt.content_version == "v1"

    # Hint temper applies to the hinted question only (Req 7.2).
    expected_score = tempered_gain(
        MASTERY_GAIN_PER_CORRECT_ANSWER, hint_revealed_before_submission=True
    ) + MASTERY_GAIN_PER_CORRECT_ANSWER * (len(_QUESTIONS) - 1)
    stored = w.mastery.get(user, _CONCEPT)
    assert stored is not None
    assert stored.score == expected_score
    assert stored.score >= MASTERY_THRESHOLDS.practiced
    assert stored.state is MasteryState.PRACTICED
    assert {e.source for e in stored.events} == {"hint", "quiz"}

    # Practiced started the one review entry; reaching it unlocked the dependent module.
    schedule = w.reviews.get(user, _CONCEPT)
    assert schedule is not None
    assert schedule.due_at == _T0 + INITIAL_REVIEW_INTERVAL
    after = w.get_progress(user, enrollment_id)
    assert [m.state for m in after.modules] == [MODULE_STATE_UNLOCKED, MODULE_STATE_UNLOCKED]
    assert after.modules[0].completions == (LessonCompletionRecord("mod-1", "les-1", _T0),)

    # Review lifecycle driven by the Clock (Req 9.6): not due until the instant arrives.
    w.clock.advance(INITIAL_REVIEW_INTERVAL - timedelta(seconds=1))
    assert not is_due(schedule, clock=w.clock)
    with pytest.raises(ReviewNotDueError):
        w.start_review(user)
    w.clock.advance(timedelta(seconds=1))
    assert is_due(schedule, clock=w.clock) and not is_overdue(schedule, clock=w.clock)

    # Passed review lengthens the interval and earns the review gain (Req 9.4).
    passed = w.submit_review(user, w.review_selections(user, correct=True))
    assert passed.passed
    assert passed.schedule.interval == PASSED_REVIEW_INTERVAL > INITIAL_REVIEW_INTERVAL
    assert passed.schedule.due_at == w.clock.now() + PASSED_REVIEW_INTERVAL
    assert passed.mastery.score == expected_score + REVIEW_PASSED_MASTERY_GAIN
    persisted = w.reviews.get(user, _CONCEPT)
    assert persisted == passed.schedule
    assert [a.passed for a in persisted.history] == [True]  # type: ignore[union-attr]
    assert len(w.reviews.list_for_user(user)) == 1  # still one entry

    # Failed review, two days overdue: decay lands first, then the interval shortens (Req 9.5).
    overdue_by = timedelta(days=2)
    w.clock.advance(PASSED_REVIEW_INTERVAL + overdue_by)
    assert overdue_duration(passed.schedule, clock=w.clock) == overdue_by
    assert is_overdue(passed.schedule, clock=w.clock)
    failed = w.submit_review(user, w.review_selections(user, correct=False))
    assert not failed.passed
    assert failed.schedule.interval == FAILED_REVIEW_INTERVAL < PASSED_REVIEW_INTERVAL
    assert failed.schedule.due_at == w.clock.now() + FAILED_REVIEW_INTERVAL
    assert failed.mastery.score == passed.mastery.score + overdue_by.days * DECAY_RATE_PER_DAY
    final_schedule = w.reviews.get(user, _CONCEPT)
    assert final_schedule is not None
    assert [a.passed for a in final_schedule.history] == [True, False]
    # Decay never relocks the dependent module (Req 2.5).
    last = w.get_progress(user, enrollment_id)
    assert last.modules[1].state == MODULE_STATE_UNLOCKED

    # Project submission: pending until the reviewer completes, then reviewed.
    pending = w.submit_project(user, pending=True)
    assert pending.status is SubmissionStatus.PENDING
    reviewed = w.submit_project(user, pending=False)
    assert reviewed.status is SubmissionStatus.REVIEWED
    stored_submission = w.attempts.get_project_submission(reviewed.submission.stable_id)
    assert stored_submission is not None and stored_submission.review_outcome == "passed"
    stored_pending = w.attempts.get_project_submission(pending.submission.stable_id)
    assert stored_pending is not None and stored_pending.review_outcome is None


def test_shared_redis_idempotency_across_two_worker_stores(engine: Engine) -> None:
    """A second store over the same Redis (another worker) replays; nothing is double-recorded."""
    w = _World(engine)
    w.enroll(_U1)
    right = w.selections(correct=True)
    first = w.attempt_quiz(_U1, right, "shared-key")

    other_worker = Redis_IdempotencyStore(
        w.redis, encode=encode_attempt_result, decode=decode_attempt_result
    )
    w.idempotency = other_worker
    replay = w.attempt_quiz(_U1, right, "shared-key")

    assert replay.replayed and replay.attempt == first.attempt
    assert len(w.attempts.list_quiz_attempts(_U1, _QUIZ)) == 1
    stored = w.mastery.get(_U1, _CONCEPT)
    assert stored is not None
    assert len(stored.events) == 1


# --- 2. Version pinning (Req 25.1, 25.2, 25.3, 26.3) --------------------------------


def _edit_quiz_to_v2(engine: Engine) -> None:
    """Sync an edit: bump the quiz version and flip which option of each question is correct."""
    with engine.begin() as c:
        c.execute(
            _T["content_provenance"]
            .update()
            .where(_T["content_provenance"].c.stable_id == "prov-quiz")
            .values(content_version="v2")
        )
        c.execute(
            _T["question_option"].update().values(is_correct=~_T["question_option"].c.is_correct)
        )


def test_new_attempt_after_an_edit_uses_the_new_version_and_keeps_old_rows(
    engine: Engine,
) -> None:
    w = _World(engine)
    v1_right = w.selections(correct=True)
    first = w.attempt_quiz(_U1, v1_right, "k1")
    assert first.attempt.content_version == "v1" and first.attempt.score == 100
    mastery_before = w.mastery.get(_U1, _CONCEPT)

    _edit_quiz_to_v2(engine)
    w.clock.advance(COOLDOWN_INTERVAL)
    second = w.attempt_quiz(_U1, v1_right, "k2")  # the old answers are wrong under v2

    assert second.attempt.content_version == "v2"
    assert second.attempt.score == 0
    stored = {a.stable_id: a for a in w.attempts.list_quiz_attempts(_U1, _QUIZ)}
    assert stored[first.attempt.stable_id].content_version == "v1"  # existing row intact
    assert stored[first.attempt.stable_id].score == 100
    assert w.mastery.get(_U1, _CONCEPT) == mastery_before


def test_in_flight_attempt_stays_pinned_to_the_version_it_started_under(
    engine: Engine,
) -> None:
    """The SQL catalog keeps only the current revision (one row per stable_id), so superseded
    revisions come from the archive; model that archive with the in-memory catalog while the
    attempt rows still land in the real repository."""
    w = _World(engine)
    v1 = w.quiz
    archive = In_Memory_CatalogRepository()
    archive.add_quiz(v1)

    _edit_quiz_to_v2(engine)
    v2 = w.catalog.get_quiz(_QUIZ)
    assert v2 is not None and v2.content_version == "v2"
    archive.revise_quiz(v2)

    result = w.attempt_quiz(
        _U1,
        w.selections(correct=True, quiz=v1),
        "pinned",
        catalog=archive,
        content_version="v1",
    )

    assert result.attempt.content_version == "v1"
    assert result.attempt.score == 100  # judged against v1, where those options are correct
    persisted = w.attempts.get_quiz_attempt(result.attempt.stable_id)
    assert persisted is not None and persisted.content_version == "v1"

    # Both revisions stay resolvable; none was deleted (Req 26.3).
    assert archive.get_quiz_at_version(_QUIZ, "v1") == v1
    assert archive.get_quiz_at_version(_QUIZ, "v2") == v2


# --- 3. Deferred archive during active use (Req 26.2, 26.3) ---------------------------


def _prov(stable_id: str, version: str = "cv_1") -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "content_version": version,
        "origin": "human",
        "reviewed_by": "usr_reviewer",
    }


def _title(text: str = "Title") -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": text}


def _authored(*, with_lesson: bool = True, lesson_version: str = "cv_1") -> dict[str, Any]:
    items: dict[str, Any] = {
        "track": [{"provenance": _prov("tr_1"), "title": _title(), "status": "published"}],
        "module": [
            {
                "provenance": _prov("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _title(),
            }
        ],
        "lesson": [],
    }
    if with_lesson:
        items["lesson"].append(
            {
                "provenance": _prov("ls_1", lesson_version),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _title(f"Lesson {lesson_version}"),
            }
        )
    return items


def _load(items: dict[str, Any], root: Path) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")
    return load_content(root)


def test_removed_item_is_archived_only_after_active_sessions_complete(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    root = tmp_path / "content"
    sync_content(store, _load(_authored(), root))
    pinned = store.start_session("attempt-1", "ls_1")

    # The lesson is edited while the session is active, then removed altogether.
    sync_content(store, _load(_authored(lesson_version="cv_2"), root))
    assert store.get("ls_1").content_version == "cv_2"  # type: ignore[union-attr]
    assert store.session_pinned_version("attempt-1") == pinned == "cv_1"

    deferred = sync_content(store, _load(_authored(with_lesson=False), root))
    assert deferred.deferred == ("ls_1",) and deferred.archived == ()
    assert store.get("ls_1").status is ContentStatus.ACTIVE  # type: ignore[union-attr]
    assert store.session_pinned_version("attempt-1") == "cv_1"  # still pinned

    store.complete_session("attempt-1")
    final = sync_content(store, _load(_authored(with_lesson=False), root))
    assert final.archived == ("ls_1",) and final.deferred == ()
    archived = store.get("ls_1")
    assert archived is not None and archived.status is ContentStatus.ARCHIVED  # never deleted


# --- 4. GDPR export and deletion across every personal-data table (Req 22) ------------


def _pk(result: Any) -> int:
    return int(result.inserted_primary_key[0])


def _internal_user_id(engine: Engine, stable_id: str) -> int:
    with engine.connect() as c:
        return int(
            c.execute(
                select(_T["user"].c.id).where(_T["user"].c.stable_id == stable_id)
            ).scalar_one()
        )


def _seed_remaining_personal_data(w: _World, user: str) -> None:
    """Fill the tables the learning journey does not reach, so all of them hold rows."""
    uid = _internal_user_id(w.engine, user)
    with w.engine.begin() as c:
        session_id = _pk(
            c.execute(
                insert(_T["lab_session"]).values(
                    stable_id=f"ls-{user}", user_id=uid, lab_id=1, status="done"
                )
            )
        )
        run_id = _pk(
            c.execute(insert(_T["lab_run"]).values(lab_session_id=session_id, status="graded"))
        )
        c.execute(
            insert(_T["lab_task_completion"]).values(lab_run_id=run_id, lab_task_id=1, passed=True)
        )
        c.execute(
            insert(_T["path_recommendation"]).values(
                user_id=uid, source="placement", ordering=json.dumps([_TRACK])
            )
        )
        c.execute(insert(_T["oauth_link"]).values(user_id=uid, provider="github", subject=user))
        c.execute(insert(_T["role_assignment"]).values(user_id=uid, role="learner"))
    w.assessments.save_placement(AssessmentRecord(user, _TRACK, AssessmentStatus.COMPLETED))
    w.assessments.save_final_assessment(AssessmentRecord(user, _TRACK, AssessmentStatus.PENDING))


def _open_session(w: _World, user: str, ref: str) -> None:
    expires = w.clock.now() + SESSION_DURATION
    w.session_store.create(ref, user, expires_at=expires)
    w.durable_sessions.add(SessionRecord(ref, user, expires))


def _populate_learner(w: _World, user: str) -> None:
    """Run a compact journey that writes every learning table for ``user``."""
    enrollment_id = w.enroll(user)
    w.progress.for_enrollment(enrollment_id).add_lesson_completion(
        LessonCompletionRecord("mod-1", "les-1", w.clock.now())
    )
    w.reveal_hint(user, _QUESTIONS[0])
    w.attempt_quiz(user, w.selections(correct=True), f"key-{user}")
    w.clock.advance(INITIAL_REVIEW_INTERVAL)
    w.submit_review(user, w.review_selections(user, correct=True))
    w.submit_project(user, pending=False)
    _seed_remaining_personal_data(w, user)


def test_export_and_deletion_cover_every_personal_data_table(engine: Engine) -> None:
    w = _World(engine)
    _populate_learner(w, _U1)
    _populate_learner(w, _U2)
    _open_session(w, _U1, "ref-lea")
    _open_session(w, _U2, "ref-sam")
    gateway = Postgres_PersonalDataGateway(engine)
    storage = In_Memory_ObjectStorage()

    # Export includes the learner's rows in every personal-data table (Req 22.1).
    exported = ExportUserDataUseCase(personal_data=gateway, object_storage=storage).execute(
        ExportUserDataRequest(user_stable_id=_U1)
    )
    document = json.loads(storage.get(exported.storage_key).data)
    assert set(document) == set(PERSONAL_DATA_TABLES)
    for table in PERSONAL_DATA_TABLES:
        assert document[table], f"export is missing rows for {table}"
        assert all("id" not in row for row in document[table]), table
    kept_before = {t: len(rows) for t, rows in gateway.export_user_data(_U2).items()}
    assert all(count >= 1 for count in kept_before.values())

    # Deletion removes everything of that learner and revokes their sessions (Req 22.2).
    assert w.session_store.resolve("ref-lea") is not None
    result = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=w.durable_sessions,
        session_store=w.session_store,
        clock=w.clock,
    ).execute(DeleteAccountRequest(user_stable_id=_U1, confirmed=True))

    assert result.deleted and result.rows_removed > 0 and result.sessions_revoked >= 1
    assert w.session_store.resolve("ref-lea") is None
    gone = gateway.export_user_data(_U1)
    for table in PERSONAL_DATA_TABLES:
        assert gone[table] == [], table

    # The other learner is untouched, including their live session (Req 22.3).
    kept_after = {t: len(rows) for t, rows in gateway.export_user_data(_U2).items()}
    assert kept_after == kept_before
    assert w.session_store.resolve("ref-sam") is not None
