"""The PostgreSQL learning repositories (task 20.1, Req 17.2, 17.4, 17.5, 18.1).

These adapters implement the domain learning ports over the D3 schema in
:mod:`~edu_it_infrastructure.persistence.metadata` with SQLAlchemy Core:

- :class:`Postgres_CatalogRepository` ->
  :class:`~edu_it_domain.ports.catalog.CatalogRepository` (catalog group, D3 section 6).
- :class:`Postgres_LessonRepository` -> :class:`~edu_it_domain.ports.lessons.LessonRepository`.
- :class:`Postgres_AssessmentRepository` ->
  :class:`~edu_it_domain.ports.assessments.AssessmentRepository` (D3 section 12).
- :class:`Postgres_EnrollmentRepository` ->
  :class:`~edu_it_domain.ports.enrollment.EnrollmentRepository`.
- :class:`Postgres_ProgressRepository` -> :class:`~edu_it_domain.ports.progress.ProgressRepository`.
- :class:`Postgres_MasteryRepository` -> :class:`~edu_it_domain.ports.mastery.MasteryRepository`.
- :class:`Postgres_ReviewRepository` -> :class:`~edu_it_domain.ports.review.ReviewRepository`.
- :class:`Postgres_AttemptRepository` -> :class:`~edu_it_domain.ports.attempts.AttemptRepository`.

Conventions shared by every adapter here:

- **Stable ids only.** The domain addresses every record by ``stable_id`` (Req 17.4).
  Each adapter resolves a ``stable_id`` to the internal numeric key inside the
  operation and maps rows back to domain values without ever surfacing ``id``. Lookups
  hit the ``stable_id`` unique index (D3 section 1.1).
- **Keyset pagination.** List operations that back list endpoints order by a stable
  sort key tie-broken by ``stable_id`` and expose ``*_page`` variants taking an
  ``after`` key (never an offset), each served by a composite index declared on the
  schema (Req 17.5).
- **Database-enforced invariants.** One enrollment per learner x track, one
  module-progress row per enrollment x module, one lesson completion per module
  progress x lesson, one mastery / review-schedule row per learner x concept, and a
  unique ``Idempotency-Key`` on ``quiz_attempt`` are unique constraints, so a
  concurrent duplicate fails at the store rather than relying on an in-process lock
  (Req 18.1, 23.2).
- **Append-only history.** ``mastery_event`` and ``review_attempt`` only grow; a
  save that would drop or rewrite stored history is rejected.

Like the identity repositories the adapters are database-agnostic SQLAlchemy Core
(PostgreSQL in production, SQLite in unit tests) and each operation runs in its own
short transaction (``engine.begin``).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

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
    StableId,
)
from edu_it_domain.ports.assessments import (
    AssessmentRecord,
    AssessmentRepository,
    AssessmentStatus,
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
from edu_it_domain.ports.lessons import LessonRecord, LessonRepository
from edu_it_domain.ports.mastery import HintRevealRecord, MasteryRepository
from edu_it_domain.ports.progress import (
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)
from edu_it_domain.ports.review import ReviewRepository
from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.repositories import _as_aware, _EngineHandle

# The ``persistence`` package re-exports the ``MetaData`` object under the name
# ``metadata``, shadowing the submodule of the same name, so the table module is
# fetched by its full dotted name instead.
m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = [
    "DuplicateQuizAttemptError",
    "LessonContentSource",
    "Postgres_AssessmentRepository",
    "Postgres_AttemptRepository",
    "Postgres_CatalogRepository",
    "Postgres_EnrollmentRepository",
    "Postgres_LessonRepository",
    "Postgres_MasteryRepository",
    "Postgres_ProgressRepository",
    "Postgres_ReviewRepository",
]

#: Default page size for the ``*_page`` keyset methods.
DEFAULT_PAGE_SIZE: int = 50


class DuplicateQuizAttemptError(ValueError):
    """A quiz attempt could not be stored because its key already exists.

    Raised by :meth:`Postgres_AttemptRepository.add_quiz_attempt` when either the
    attempt ``stable_id`` or the ``Idempotency-Key`` is already used.
    :attr:`idempotency_key_conflict` is ``True`` for the latter, so the caller can
    replay the stored attempt instead of recording a second one (Req 18.1, 23.1).
    """

    def __init__(self, message: str, *, idempotency_key_conflict: bool) -> None:
        super().__init__(message)
        self.idempotency_key_conflict = idempotency_key_conflict


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _Base:
    """Holds the lazily-created engine shared by every learning adapter."""

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL."""
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine


def _internal_id(connection: Connection, table: Any, stable_id: StableId) -> int | None:
    """Resolve ``stable_id`` to the table's internal numeric key, or ``None``."""
    return connection.execute(
        select(table.c.id).where(table.c.stable_id == stable_id)
    ).scalar_one_or_none()


def _require_id(connection: Connection, table: Any, stable_id: StableId, label: str) -> int:
    """Resolve ``stable_id`` or raise :class:`ValueError` naming the missing ``label``."""
    internal = _internal_id(connection, table, stable_id)
    if internal is None:
        raise ValueError(f"unknown {label} {stable_id!r}")
    return internal


def _json_tuple(text: str | None) -> tuple[str, ...]:
    """Decode a JSON array of strings column, treating ``NULL``/empty as no entries."""
    if not text:
        return ()
    return tuple(str(item) for item in json.loads(text))


def _page_size(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return limit


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    """Compare two instants ignoring backend naive/aware differences."""
    if a is None or b is None:
        return a is b
    return _as_aware(a) == _as_aware(b)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class Postgres_CatalogRepository(_Base, CatalogRepository):
    """A read-only :class:`CatalogRepository` over the D3 catalog tables.

    Content is written by ``content sync``; this adapter only reads it. A
    ``question_option`` has no ``stable_id`` so it is identified by its row id as the
    per-question ``option_id`` (the one place a row id is exposed, as the domain model
    defines it). A ``rubric_criterion`` is likewise not addressable, so it is given the
    derived identifier ``<rubric stable_id>#<position>`` (1-based, in authored order).
    """

    def get_module(self, stable_id: StableId) -> ModuleRecord | None:
        """Return the module with ``stable_id``, or ``None`` when absent."""
        statement = self._module_select().where(m.module.c.stable_id == stable_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else _module_record(row)

    def list_modules_for_track(self, track_id: StableId) -> tuple[ModuleRecord, ...]:
        """Return every module of ``track_id`` ordered by ordinal then ``stable_id``."""
        statement = (
            self._module_select()
            .where(m.track.c.stable_id == track_id)
            .order_by(m.module.c.ordinal, m.module.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_module_record(row) for row in rows)

    def list_modules_page(
        self,
        track_id: StableId,
        *,
        after: tuple[int, StableId] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[ModuleRecord, ...]:
        """Return up to ``limit`` modules of ``track_id`` strictly after the keyset ``after``.

        The sort key is ``(ordinal, stable_id)``; ``after`` is the key of the last
        module of the previous page (Req 17.5).
        """
        statement = self._module_select().where(m.track.c.stable_id == track_id)
        if after is not None:
            ordinal, stable_id = after
            statement = statement.where(
                or_(
                    m.module.c.ordinal > ordinal,
                    and_(m.module.c.ordinal == ordinal, m.module.c.stable_id > stable_id),
                )
            )
        statement = statement.order_by(m.module.c.ordinal, m.module.c.stable_id).limit(
            _page_size(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_module_record(row) for row in rows)

    def get_concept(self, stable_id: StableId) -> ConceptRecord | None:
        """Return the concept with ``stable_id`` and its direct prerequisite edges."""
        required = m.concept.alias("required")
        with self._engine.connect() as connection:
            concept_id = _internal_id(connection, m.concept, stable_id)
            if concept_id is None:
                return None
            requires: list[str] = list(
                connection.execute(
                    select(required.c.stable_id)
                    .select_from(
                        m.prerequisite.join(
                            required, m.prerequisite.c.requires_concept_id == required.c.id
                        )
                    )
                    .where(m.prerequisite.c.concept_id == concept_id)
                    .order_by(required.c.stable_id)
                )
                .scalars()
                .all()
            )
        return ConceptRecord(stable_id=stable_id, requires=tuple(requires))

    def get_quiz(self, stable_id: StableId) -> QuizRecord | None:
        """Return the quiz with ``stable_id`` and its questions/options, or ``None``."""
        with self._engine.connect() as connection:
            return self._load_quiz(connection, stable_id)

    def get_quiz_at_version(self, stable_id: StableId, content_version: str) -> QuizRecord | None:
        """Return the quiz when its current revision is ``content_version``, else ``None``.

        The D3 schema keeps one row per ``stable_id`` (``stable_id`` is unique), so
        only the current revision is retrievable here; superseded revisions are the
        archive ``content sync`` retains (Req 26.3) and are not stored in the catalog
        tables.
        """
        quiz = self.get_quiz(stable_id)
        if quiz is None or quiz.content_version != content_version:
            return None
        return quiz

    def list_questions_for_concept(self, concept_id: StableId) -> tuple[QuestionRecord, ...]:
        """Return every question covering ``concept_id``, ordered by ``stable_id``."""
        # Narrow in SQL on the JSON-encoded element (quotes are escaped inside JSON
        # strings, so '"id"' can only match a whole element), then confirm exactly.
        needle = json.dumps(concept_id)
        pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        statement = (
            select(m.question.c.id, m.question.c.stable_id, m.question.c.concept_stable_ids)
            .where(m.question.c.concept_stable_ids.like(pattern, escape="\\"))
            .order_by(m.question.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
            matched = [row for row in rows if concept_id in _json_tuple(row["concept_stable_ids"])]
            options = self._options_by_question(connection, [row["id"] for row in matched])
        return tuple(
            QuestionRecord(
                stable_id=row["stable_id"],
                options=options.get(row["id"], ()),
                concept_ids=_json_tuple(row["concept_stable_ids"]),
            )
            for row in matched
        )

    def get_rubric_for_project(self, project_id: StableId) -> Rubric | None:
        """Return the rubric scoring ``project_id``, or ``None`` when absent."""
        statement = (
            select(m.rubric.c.id, m.rubric.c.stable_id)
            .select_from(m.rubric.join(m.project, m.rubric.c.project_id == m.project.c.id))
            .where(m.project.c.stable_id == project_id)
        )
        with self._engine.connect() as connection:
            rubric_row = connection.execute(statement).mappings().first()
            if rubric_row is None:
                return None
            points: list[int] = list(
                connection.execute(
                    select(m.rubric_criterion.c.max_points)
                    .where(m.rubric_criterion.c.rubric_id == rubric_row["id"])
                    .order_by(m.rubric_criterion.c.id)
                )
                .scalars()
                .all()
            )
        rubric_id = rubric_row["stable_id"]
        criteria = tuple(
            RubricCriterion(stable_id=f"{rubric_id}#{position}", max_points=int(max_points))
            for position, max_points in enumerate(points, start=1)
        )
        return Rubric(stable_id=rubric_id, project_id=project_id, criteria=criteria)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _module_select() -> Any:
        return select(
            m.module.c.stable_id,
            m.module.c.ordinal,
            m.module.c.prerequisite_concept_ids,
            m.track.c.stable_id.label("track_stable_id"),
        ).select_from(m.module.join(m.track, m.module.c.track_id == m.track.c.id))

    def _load_quiz(self, connection: Connection, stable_id: StableId) -> QuizRecord | None:
        row = (
            connection.execute(
                select(
                    m.quiz.c.id,
                    m.lesson.c.stable_id.label("lesson_stable_id"),
                    m.content_provenance.c.content_version,
                )
                .select_from(
                    m.quiz.join(m.lesson, m.quiz.c.lesson_id == m.lesson.c.id).join(
                        m.content_provenance, m.quiz.c.provenance_id == m.content_provenance.c.id
                    )
                )
                .where(m.quiz.c.stable_id == stable_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        question_rows = (
            connection.execute(
                select(m.question.c.id, m.question.c.stable_id, m.question.c.concept_stable_ids)
                .where(m.question.c.quiz_id == row["id"])
                .order_by(m.question.c.id)
            )
            .mappings()
            .all()
        )
        options = self._options_by_question(connection, [q["id"] for q in question_rows])
        questions = tuple(
            QuestionRecord(
                stable_id=q["stable_id"],
                options=options.get(q["id"], ()),
                concept_ids=_json_tuple(q["concept_stable_ids"]),
            )
            for q in question_rows
        )
        return QuizRecord(
            stable_id=stable_id,
            lesson_id=row["lesson_stable_id"],
            questions=questions,
            content_version=row["content_version"],
        )

    @staticmethod
    def _options_by_question(
        connection: Connection, question_ids: Sequence[int]
    ) -> dict[int, tuple[QuestionOption, ...]]:
        if not question_ids:
            return {}
        rows = (
            connection.execute(
                select(
                    m.question_option.c.id,
                    m.question_option.c.question_id,
                    m.question_option.c.is_correct,
                )
                .where(m.question_option.c.question_id.in_(list(question_ids)))
                .order_by(m.question_option.c.id)
            )
            .mappings()
            .all()
        )
        grouped: dict[int, list[QuestionOption]] = {}
        for option in rows:
            grouped.setdefault(option["question_id"], []).append(
                QuestionOption(option_id=int(option["id"]), is_correct=bool(option["is_correct"]))
            )
        return {key: tuple(value) for key, value in grouped.items()}


def _module_record(row: Any) -> ModuleRecord:
    return ModuleRecord(
        stable_id=row["stable_id"],
        track_id=row["track_stable_id"],
        ordinal=int(row["ordinal"]),
        prerequisite_concept_ids=_json_tuple(row["prerequisite_concept_ids"]),
    )


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


class Postgres_EnrollmentRepository(_Base, EnrollmentRepository):
    """An :class:`EnrollmentRepository` over the ``enrollment`` table (Req 17.1)."""

    def add(self, enrollment: EnrollmentRecord) -> None:
        """Insert ``enrollment``; reject a reused ``stable_id`` or a second ``(user, track)``.

        The pre-checks give clear errors; the ``stable_id`` and ``(user_id, track_id)``
        unique constraints are the backstop that makes a concurrent duplicate fail.
        """
        if self.get(enrollment.stable_id) is not None:
            raise ValueError(f"enrollment with stable_id {enrollment.stable_id!r} already exists")
        try:
            with self._engine.begin() as connection:
                user_id = _require_id(connection, m.user, enrollment.user_id, "user")
                track_id = _require_id(connection, m.track, enrollment.track_id, "track")
                existing = connection.execute(
                    select(m.enrollment.c.id).where(
                        and_(
                            m.enrollment.c.user_id == user_id,
                            m.enrollment.c.track_id == track_id,
                        )
                    )
                ).first()
                if existing is not None:
                    raise ValueError(
                        f"user {enrollment.user_id!r} is already enrolled in "
                        f"track {enrollment.track_id!r}"
                    )
                connection.execute(
                    m.enrollment.insert().values(
                        stable_id=enrollment.stable_id,
                        user_id=user_id,
                        track_id=track_id,
                        enrolled_at=enrollment.enrolled_at,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError(f"enrollment {enrollment.stable_id!r} already exists") from exc

    def get(self, stable_id: StableId) -> EnrollmentRecord | None:
        """Return the enrollment with ``stable_id``, or ``None`` when absent."""
        return self._fetch_one(m.enrollment.c.stable_id == stable_id)

    def get_for_user_and_track(
        self,
        user_id: StableId,
        track_id: StableId,
    ) -> EnrollmentRecord | None:
        """Return the learner's enrollment in the track, or ``None`` when absent."""
        return self._fetch_one(and_(m.user.c.stable_id == user_id, m.track.c.stable_id == track_id))

    def list_for_user(self, user_id: StableId) -> tuple[EnrollmentRecord, ...]:
        """Return every enrollment owned by ``user_id``, oldest first."""
        return self.list_for_user_page(user_id, limit=_UNBOUNDED)

    def list_for_user_page(
        self,
        user_id: StableId,
        *,
        after: tuple[datetime, StableId] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[EnrollmentRecord, ...]:
        """Return up to ``limit`` enrollments strictly after the keyset ``after``.

        The sort key is ``(enrolled_at, stable_id)`` (Req 17.5).
        """
        statement = self._select().where(m.user.c.stable_id == user_id)
        if after is not None:
            enrolled_at, stable_id = after
            statement = statement.where(
                or_(
                    m.enrollment.c.enrolled_at > enrolled_at,
                    and_(
                        m.enrollment.c.enrolled_at == enrolled_at,
                        m.enrollment.c.stable_id > stable_id,
                    ),
                )
            )
        statement = statement.order_by(m.enrollment.c.enrolled_at, m.enrollment.c.stable_id).limit(
            _page_size(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_enrollment_record(row) for row in rows)

    def _fetch_one(self, condition: Any) -> EnrollmentRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(self._select().where(condition)).mappings().first()
        return None if row is None else _enrollment_record(row)

    @staticmethod
    def _select() -> Any:
        return select(
            m.enrollment.c.stable_id,
            m.enrollment.c.enrolled_at,
            m.user.c.stable_id.label("user_stable_id"),
            m.track.c.stable_id.label("track_stable_id"),
        ).select_from(
            m.enrollment.join(m.user, m.enrollment.c.user_id == m.user.c.id).join(
                m.track, m.enrollment.c.track_id == m.track.c.id
            )
        )


#: Sentinel limit for the unpaged ``list_*`` methods (effectively every row).
_UNBOUNDED: int = 2**31 - 1


def _enrollment_record(row: Any) -> EnrollmentRecord:
    return EnrollmentRecord(
        stable_id=row["stable_id"],
        user_id=row["user_stable_id"],
        track_id=row["track_stable_id"],
        enrolled_at=_as_aware(row["enrolled_at"]),
    )


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class Postgres_ProgressRepository(_Base, ProgressRepository):
    """A :class:`ProgressRepository` over ``module_progress`` and ``lesson_completion``.

    The port reads and writes lesson completions by ``module_id`` alone, but in D3 a
    completion hangs off a ``module_progress`` row, which belongs to one enrollment.
    To never attribute or expose another learner's completions, the adapter resolves
    the module's progress row from an enrollment: either the one bound with
    :meth:`for_enrollment`, or - when unbound - the module's single progress row. An
    unbound call where several learners have progress on the module raises
    :class:`ValueError` instead of guessing.
    """

    def __init__(self, engine: Engine | str, *, enrollment_id: StableId | None = None) -> None:
        """Create the repository, optionally bound to the enrollment ``enrollment_id``."""
        super().__init__(engine)
        self._enrollment_id = enrollment_id

    def for_enrollment(self, enrollment_id: StableId) -> Postgres_ProgressRepository:
        """Return a repository sharing this engine, bound to ``enrollment_id``."""
        bound = Postgres_ProgressRepository(self._handle.engine, enrollment_id=enrollment_id)
        return bound

    def get_module_progress(
        self,
        enrollment_id: StableId,
        module_id: StableId,
    ) -> ModuleProgressRecord | None:
        """Return the module's progress under ``enrollment_id``, or ``None``."""
        statement = self._progress_select().where(
            and_(m.enrollment.c.stable_id == enrollment_id, m.module.c.stable_id == module_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else _module_progress_record(row)

    def list_module_progress(self, enrollment_id: StableId) -> tuple[ModuleProgressRecord, ...]:
        """Return every module-progress row under ``enrollment_id``, in module order."""
        statement = (
            self._progress_select()
            .where(m.enrollment.c.stable_id == enrollment_id)
            .order_by(m.module.c.ordinal, m.module.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_module_progress_record(row) for row in rows)

    def upsert_module_progress(self, progress: ModuleProgressRecord) -> None:
        """Insert or update the ``(enrollment, module)`` row for ``progress``."""
        for attempt in range(2):
            try:
                with self._engine.begin() as connection:
                    enrollment_pk = _require_id(
                        connection, m.enrollment, progress.enrollment_id, "enrollment"
                    )
                    module_pk = _require_id(connection, m.module, progress.module_id, "module")
                    key = and_(
                        m.module_progress.c.enrollment_id == enrollment_pk,
                        m.module_progress.c.module_id == module_pk,
                    )
                    updated = connection.execute(
                        m.module_progress.update()
                        .where(key)
                        .values(state=progress.state, tested_out=progress.tested_out)
                    )
                    if updated.rowcount == 0:
                        connection.execute(
                            m.module_progress.insert().values(
                                enrollment_id=enrollment_pk,
                                module_id=module_pk,
                                state=progress.state,
                                tested_out=progress.tested_out,
                            )
                        )
                return
            except IntegrityError:
                # A concurrent insert won the unique (enrollment, module) race; the
                # retry then takes the update path.
                if attempt == 1:
                    raise

    def add_lesson_completion(self, completion: LessonCompletionRecord) -> None:
        """Record ``completion`` idempotently per ``(module_progress, lesson)``.

        A lesson already completed keeps its earliest completion (Req 14.3, 26.1).
        """
        try:
            with self._engine.begin() as connection:
                progress_pk = self._resolve_progress_pk(connection, completion.module_id)
                lesson_pk = _require_id(connection, m.lesson, completion.lesson_id, "lesson")
                existing = connection.execute(
                    select(m.lesson_completion.c.id).where(
                        and_(
                            m.lesson_completion.c.module_progress_id == progress_pk,
                            m.lesson_completion.c.lesson_id == lesson_pk,
                        )
                    )
                ).first()
                if existing is not None:
                    return
                connection.execute(
                    m.lesson_completion.insert().values(
                        module_progress_id=progress_pk,
                        lesson_id=lesson_pk,
                        completed_at=completion.completed_at,
                    )
                )
        except IntegrityError:  # pragma: no cover - concurrent duplicate is a no-op
            return

    def list_lesson_completions(
        self,
        module_id: StableId,
    ) -> tuple[LessonCompletionRecord, ...]:
        """Return the lesson completions under the module's progress row, oldest first."""
        with self._engine.connect() as connection:
            module_pk = _internal_id(connection, m.module, module_id)
            if module_pk is None:
                return ()
            progress_pk = self._resolve_progress_pk(connection, module_id, required=False)
            if progress_pk is None:
                return ()
            rows = (
                connection.execute(
                    select(m.lesson.c.stable_id, m.lesson_completion.c.completed_at)
                    .select_from(
                        m.lesson_completion.join(
                            m.lesson, m.lesson_completion.c.lesson_id == m.lesson.c.id
                        )
                    )
                    .where(m.lesson_completion.c.module_progress_id == progress_pk)
                    .order_by(m.lesson_completion.c.completed_at, m.lesson.c.stable_id)
                )
                .mappings()
                .all()
            )
        return tuple(
            LessonCompletionRecord(
                module_id=module_id,
                lesson_id=row["stable_id"],
                completed_at=_as_aware(row["completed_at"]),
            )
            for row in rows
            if row["completed_at"] is not None
        )

    # -- internals ----------------------------------------------------------

    def _resolve_progress_pk(
        self, connection: Connection, module_id: StableId, *, required: bool = True
    ) -> int | None:
        """Resolve the ``module_progress`` row a completion belongs to, never guessing."""
        statement = (
            select(m.module_progress.c.id)
            .select_from(
                m.module_progress.join(
                    m.module, m.module_progress.c.module_id == m.module.c.id
                ).join(m.enrollment, m.module_progress.c.enrollment_id == m.enrollment.c.id)
            )
            .where(m.module.c.stable_id == module_id)
        )
        if self._enrollment_id is not None:
            statement = statement.where(m.enrollment.c.stable_id == self._enrollment_id)
        ids: list[int] = list(connection.execute(statement.limit(2)).scalars().all())
        if len(ids) > 1:
            raise ValueError(
                f"module {module_id!r} has progress for several enrollments; "
                "bind the repository with for_enrollment()"
            )
        if not ids:
            if required:
                raise ValueError(f"no module progress recorded for module {module_id!r}")
            return None
        return int(ids[0])

    @staticmethod
    def _progress_select() -> Any:
        return select(
            m.enrollment.c.stable_id.label("enrollment_stable_id"),
            m.module.c.stable_id.label("module_stable_id"),
            m.module_progress.c.state,
            m.module_progress.c.tested_out,
        ).select_from(
            m.module_progress.join(
                m.enrollment, m.module_progress.c.enrollment_id == m.enrollment.c.id
            ).join(m.module, m.module_progress.c.module_id == m.module.c.id)
        )


def _module_progress_record(row: Any) -> ModuleProgressRecord:
    return ModuleProgressRecord(
        enrollment_id=row["enrollment_stable_id"],
        module_id=row["module_stable_id"],
        state=row["state"],
        tested_out=bool(row["tested_out"]),
    )


# ---------------------------------------------------------------------------
# Mastery
# ---------------------------------------------------------------------------


class Postgres_MasteryRepository(_Base, MasteryRepository):
    """A :class:`MasteryRepository` over ``mastery``, ``mastery_event`` and ``hint_reveal``."""

    def get(self, user_id: StableId, concept_id: StableId) -> Mastery | None:
        """Return the learner's mastery of the concept with its events, or ``None``."""
        statement = self._select().where(
            and_(m.user.c.stable_id == user_id, m.concept.c.stable_id == concept_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            events = self._events(connection, [row["id"]])
        return _mastery(row, events.get(row["id"], ()))

    def list_for_user(self, user_id: StableId) -> tuple[Mastery, ...]:
        """Return every tracked mastery for ``user_id``, ordered by concept ``stable_id``."""
        statement = (
            self._select().where(m.user.c.stable_id == user_id).order_by(m.concept.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
            events = self._events(connection, [row["id"] for row in rows])
        return tuple(_mastery(row, events.get(row["id"], ())) for row in rows)

    def save(self, mastery: Mastery) -> None:
        """Upsert ``mastery`` and append its new events; reject rewritten history (Req 5.7)."""
        for attempt in range(2):
            try:
                with self._engine.begin() as connection:
                    user_pk = _require_id(connection, m.user, mastery.user_id, "user")
                    concept_pk = _require_id(connection, m.concept, mastery.concept_id, "concept")
                    key = and_(m.mastery.c.user_id == user_pk, m.mastery.c.concept_id == concept_pk)
                    mastery_pk = connection.execute(
                        select(m.mastery.c.id).where(key)
                    ).scalar_one_or_none()
                    if mastery_pk is None:
                        result = connection.execute(
                            m.mastery.insert().values(
                                user_id=user_pk,
                                concept_id=concept_pk,
                                score=mastery.score,
                                state=mastery.state.value,
                            )
                        )
                        mastery_pk = int(result.inserted_primary_key[0])  # type: ignore[index]
                        stored: tuple[MasteryEvent, ...] = ()
                    else:
                        stored = self._events(connection, [mastery_pk]).get(mastery_pk, ())
                        if not _extends(stored, mastery.events, _same_event):
                            raise ValueError(
                                "mastery_event history is append-only; a save may only extend "
                                f"the existing events for ({mastery.user_id!r}, "
                                f"{mastery.concept_id!r})"
                            )
                        connection.execute(
                            m.mastery.update()
                            .where(m.mastery.c.id == mastery_pk)
                            .values(score=mastery.score, state=mastery.state.value)
                        )
                    for event in mastery.events[len(stored) :]:
                        connection.execute(
                            m.mastery_event.insert().values(
                                mastery_id=mastery_pk,
                                delta=event.delta,
                                source=event.source,
                                occurred_at=event.occurred_at,
                            )
                        )
                return
            except IntegrityError:
                if attempt == 1:
                    raise

    def add_hint_reveal(self, reveal: HintRevealRecord) -> None:
        """Record ``reveal`` as a revealed hint, append-only (Req 7.1)."""
        with self._engine.begin() as connection:
            user_pk = _require_id(connection, m.user, reveal.user_id, "user")
            question_pk = _require_id(connection, m.question, reveal.question_id, "question")
            connection.execute(
                m.hint_reveal.insert().values(
                    user_id=user_pk, question_id=question_pk, revealed_at=reveal.revealed_at
                )
            )

    def list_hint_reveals(self, user_id: StableId) -> tuple[HintRevealRecord, ...]:
        """Return every hint reveal by ``user_id``, oldest first."""
        statement = (
            select(m.question.c.stable_id, m.hint_reveal.c.revealed_at)
            .select_from(
                m.hint_reveal.join(m.user, m.hint_reveal.c.user_id == m.user.c.id).join(
                    m.question, m.hint_reveal.c.question_id == m.question.c.id
                )
            )
            .where(m.user.c.stable_id == user_id)
            .order_by(m.hint_reveal.c.revealed_at, m.hint_reveal.c.id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            HintRevealRecord(
                user_id=user_id,
                question_id=row["stable_id"],
                revealed_at=_as_aware(row["revealed_at"]),
            )
            for row in rows
            if row["revealed_at"] is not None
        )

    @staticmethod
    def _select() -> Any:
        return select(
            m.mastery.c.id,
            m.mastery.c.score,
            m.mastery.c.state,
            m.user.c.stable_id.label("user_stable_id"),
            m.concept.c.stable_id.label("concept_stable_id"),
        ).select_from(
            m.mastery.join(m.user, m.mastery.c.user_id == m.user.c.id).join(
                m.concept, m.mastery.c.concept_id == m.concept.c.id
            )
        )

    @staticmethod
    def _events(
        connection: Connection, mastery_ids: Sequence[int]
    ) -> dict[int, tuple[MasteryEvent, ...]]:
        if not mastery_ids:
            return {}
        rows = (
            connection.execute(
                select(
                    m.mastery_event.c.mastery_id,
                    m.mastery_event.c.delta,
                    m.mastery_event.c.source,
                    m.mastery_event.c.occurred_at,
                )
                .where(m.mastery_event.c.mastery_id.in_(list(mastery_ids)))
                .order_by(m.mastery_event.c.id)
            )
            .mappings()
            .all()
        )
        grouped: dict[int, list[MasteryEvent]] = {}
        for row in rows:
            grouped.setdefault(row["mastery_id"], []).append(
                MasteryEvent(
                    delta=int(row["delta"]),
                    source=row["source"],
                    occurred_at=_as_aware(row["occurred_at"]),
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}


def _mastery(row: Any, events: tuple[MasteryEvent, ...]) -> Mastery:
    return Mastery(
        user_id=row["user_stable_id"],
        concept_id=row["concept_stable_id"],
        score=int(row["score"]),
        state=MasteryState(row["state"]),
        events=events,
    )


def _same_event(a: MasteryEvent, b: MasteryEvent) -> bool:
    return (
        a.delta == b.delta and a.source == b.source and _same_instant(a.occurred_at, b.occurred_at)
    )


def _extends[T](stored: tuple[T, ...], incoming: tuple[T, ...], same: Any) -> bool:
    """Return whether ``incoming`` extends ``stored`` without rewriting it."""
    return len(incoming) >= len(stored) and all(
        same(old, new) for old, new in zip(stored, incoming, strict=False)
    )


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class Postgres_ReviewRepository(_Base, ReviewRepository):
    """A :class:`ReviewRepository` over ``review_schedule`` and ``review_attempt``.

    ``days_overdue`` is a NOT NULL column in D3 but, per Req 9.6, overdue-ness derives
    purely from the Clock and ``due_at``; the adapter therefore stores ``0`` and the
    domain recomputes it, never reading a stale precomputed value.
    """

    def get(self, user_id: StableId, concept_id: StableId) -> ReviewSchedule | None:
        """Return the learner's review schedule for the concept, or ``None``."""
        statement = self._select().where(
            and_(m.user.c.stable_id == user_id, m.concept.c.stable_id == concept_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            attempts = self._attempts(connection, [row["id"]])
        return _schedule(row, attempts.get(row["id"], ()))

    def list_for_user(self, user_id: StableId) -> tuple[ReviewSchedule, ...]:
        """Return every review schedule for ``user_id``, ordered by ``due_at`` then concept."""
        statement = (
            self._select()
            .where(m.user.c.stable_id == user_id)
            .order_by(m.review_schedule.c.due_at, m.concept.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
            attempts = self._attempts(connection, [row["id"] for row in rows])
        return tuple(_schedule(row, attempts.get(row["id"], ())) for row in rows)

    def save(self, schedule: ReviewSchedule) -> None:
        """Upsert the single entry for ``(user, concept)`` and append new attempts."""
        interval_seconds = int(schedule.interval.total_seconds())
        for attempt in range(2):
            try:
                with self._engine.begin() as connection:
                    user_pk = _require_id(connection, m.user, schedule.user_id, "user")
                    concept_pk = _require_id(connection, m.concept, schedule.concept_id, "concept")
                    schedule_pk = connection.execute(
                        select(m.review_schedule.c.id).where(
                            and_(
                                m.review_schedule.c.user_id == user_pk,
                                m.review_schedule.c.concept_id == concept_pk,
                            )
                        )
                    ).scalar_one_or_none()
                    if schedule_pk is None:
                        result = connection.execute(
                            m.review_schedule.insert().values(
                                user_id=user_pk,
                                concept_id=concept_pk,
                                due_at=schedule.due_at,
                                days_overdue=0,
                                interval_seconds=interval_seconds,
                            )
                        )
                        schedule_pk = int(result.inserted_primary_key[0])  # type: ignore[index]
                        stored: tuple[ReviewAttempt, ...] = ()
                    else:
                        stored = self._attempts(connection, [schedule_pk]).get(schedule_pk, ())
                        if not _extends(stored, schedule.history, _same_attempt):
                            raise ValueError(
                                "review_attempt history is append-only; a save may only extend "
                                f"the existing attempts for ({schedule.user_id!r}, "
                                f"{schedule.concept_id!r})"
                            )
                        connection.execute(
                            m.review_schedule.update()
                            .where(m.review_schedule.c.id == schedule_pk)
                            .values(due_at=schedule.due_at, interval_seconds=interval_seconds)
                        )
                    for review in schedule.history[len(stored) :]:
                        connection.execute(
                            m.review_attempt.insert().values(
                                review_schedule_id=schedule_pk,
                                passed=review.passed,
                                attempted_at=review.attempted_at,
                            )
                        )
                return
            except IntegrityError:
                if attempt == 1:
                    raise

    @staticmethod
    def _select() -> Any:
        return select(
            m.review_schedule.c.id,
            m.review_schedule.c.due_at,
            m.review_schedule.c.interval_seconds,
            m.user.c.stable_id.label("user_stable_id"),
            m.concept.c.stable_id.label("concept_stable_id"),
        ).select_from(
            m.review_schedule.join(m.user, m.review_schedule.c.user_id == m.user.c.id).join(
                m.concept, m.review_schedule.c.concept_id == m.concept.c.id
            )
        )

    @staticmethod
    def _attempts(
        connection: Connection, schedule_ids: Sequence[int]
    ) -> dict[int, tuple[ReviewAttempt, ...]]:
        if not schedule_ids:
            return {}
        rows = (
            connection.execute(
                select(
                    m.review_attempt.c.review_schedule_id,
                    m.review_attempt.c.passed,
                    m.review_attempt.c.attempted_at,
                )
                .where(m.review_attempt.c.review_schedule_id.in_(list(schedule_ids)))
                .order_by(m.review_attempt.c.id)
            )
            .mappings()
            .all()
        )
        grouped: dict[int, list[ReviewAttempt]] = {}
        for row in rows:
            grouped.setdefault(row["review_schedule_id"], []).append(
                ReviewAttempt(
                    passed=bool(row["passed"]), attempted_at=_as_aware(row["attempted_at"])
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}


def _schedule(row: Any, history: tuple[ReviewAttempt, ...]) -> ReviewSchedule:
    return ReviewSchedule(
        user_id=row["user_stable_id"],
        concept_id=row["concept_stable_id"],
        due_at=_as_aware(row["due_at"]),
        interval=timedelta(seconds=int(row["interval_seconds"] or 0)),
        history=history,
    )


def _same_attempt(a: ReviewAttempt, b: ReviewAttempt) -> bool:
    return a.passed == b.passed and _same_instant(a.attempted_at, b.attempted_at)


# ---------------------------------------------------------------------------
# Attempts and submissions
# ---------------------------------------------------------------------------


class Postgres_AttemptRepository(_Base, AttemptRepository):
    """An :class:`AttemptRepository` over ``quiz_attempt``, ``answer`` and ``project_submission``.

    ``quiz_attempt.idempotency_key`` is unique, so the database - not an in-process
    lock - guarantees one stored attempt per ``Idempotency-Key`` across worker
    processes (Req 18.1, 23.2). Without an explicit key the attempt ``stable_id`` is
    used, which keeps ``stable_id`` reuse rejected through the same constraint.
    """

    def add_quiz_attempt(
        self,
        user_id: StableId,
        attempt: QuizAttempt,
        answers: tuple[Answer, ...],
        *,
        idempotency_key: str | None = None,
    ) -> None:
        """Record ``attempt`` and ``answers`` for ``user_id`` atomically.

        Raises :class:`DuplicateQuizAttemptError` (a :class:`ValueError`) when the
        attempt ``stable_id`` or the ``idempotency_key`` is already used; nothing is
        written in that case.
        """
        key = idempotency_key if idempotency_key is not None else attempt.stable_id
        if self.get_quiz_attempt(attempt.stable_id) is not None:
            raise DuplicateQuizAttemptError(
                f"quiz attempt with stable_id {attempt.stable_id!r} already exists",
                idempotency_key_conflict=False,
            )
        try:
            with self._engine.begin() as connection:
                user_pk = _require_id(connection, m.user, user_id, "user")
                quiz_pk = _require_id(connection, m.quiz, attempt.quiz_id, "quiz")
                question_pks: dict[StableId, int] = {}
                for answer in answers:
                    if answer.question_id not in question_pks:
                        question_pks[answer.question_id] = _require_id(
                            connection, m.question, answer.question_id, "question"
                        )
                result = connection.execute(
                    m.quiz_attempt.insert().values(
                        stable_id=attempt.stable_id,
                        user_id=user_pk,
                        quiz_id=quiz_pk,
                        idempotency_key=key,
                        score=attempt.score,
                        is_best=attempt.is_best,
                        attempted_at=attempt.attempted_at,
                        content_version=attempt.content_version,
                    )
                )
                attempt_pk = int(result.inserted_primary_key[0])  # type: ignore[index]
                for answer in answers:
                    connection.execute(
                        m.answer.insert().values(
                            quiz_attempt_id=attempt_pk,
                            question_id=question_pks[answer.question_id],
                            first_answered_at=answer.first_answered_at,
                            counted_for_mastery=answer.counted_for_mastery,
                        )
                    )
        except IntegrityError as exc:
            raise DuplicateQuizAttemptError(
                f"quiz attempt {attempt.stable_id!r} or idempotency key {key!r} already exists",
                idempotency_key_conflict=True,
            ) from exc

    def get_quiz_attempt(self, stable_id: StableId) -> QuizAttempt | None:
        """Return the quiz attempt with ``stable_id``, or ``None`` when absent."""
        return self._fetch_attempt(m.quiz_attempt.c.stable_id == stable_id)

    def get_quiz_attempt_by_idempotency_key(self, idempotency_key: str) -> QuizAttempt | None:
        """Return the attempt stored under ``idempotency_key``, or ``None``.

        Lets a duplicate submission replay the stored attempt (Req 18.1, 23.1).
        """
        return self._fetch_attempt(m.quiz_attempt.c.idempotency_key == idempotency_key)

    def list_quiz_attempts(
        self,
        user_id: StableId,
        quiz_id: StableId,
    ) -> tuple[QuizAttempt, ...]:
        """Return the learner's attempts at the quiz, oldest first."""
        return self.list_quiz_attempts_page(user_id, quiz_id, limit=_UNBOUNDED)

    def list_quiz_attempts_page(
        self,
        user_id: StableId,
        quiz_id: StableId,
        *,
        after: tuple[datetime, StableId] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[QuizAttempt, ...]:
        """Return up to ``limit`` attempts strictly after the keyset ``after``.

        The sort key is ``(attempted_at, stable_id)`` (Req 17.5), served by the
        ``ix_quiz_attempt_user_quiz_attempted`` composite index.
        """
        statement = self._attempt_select().where(
            and_(m.user.c.stable_id == user_id, m.quiz.c.stable_id == quiz_id)
        )
        if after is not None:
            attempted_at, stable_id = after
            statement = statement.where(
                or_(
                    m.quiz_attempt.c.attempted_at > attempted_at,
                    and_(
                        m.quiz_attempt.c.attempted_at == attempted_at,
                        m.quiz_attempt.c.stable_id > stable_id,
                    ),
                )
            )
        statement = statement.order_by(
            m.quiz_attempt.c.attempted_at, m.quiz_attempt.c.stable_id
        ).limit(_page_size(limit))
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_attempt(row) for row in rows)

    def list_answers_for_question(
        self,
        user_id: StableId,
        question_id: StableId,
    ) -> tuple[Answer, ...]:
        """Return the learner's answers to the question, oldest first."""
        first_answered = func.coalesce(m.answer.c.first_answered_at, m.quiz_attempt.c.attempted_at)
        statement = (
            select(first_answered.label("first_answered_at"), m.answer.c.counted_for_mastery)
            .select_from(
                m.answer.join(m.quiz_attempt, m.answer.c.quiz_attempt_id == m.quiz_attempt.c.id)
                .join(m.user, m.quiz_attempt.c.user_id == m.user.c.id)
                .join(m.question, m.answer.c.question_id == m.question.c.id)
            )
            .where(and_(m.user.c.stable_id == user_id, m.question.c.stable_id == question_id))
            .order_by(first_answered, m.answer.c.id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(
            Answer(
                question_id=question_id,
                first_answered_at=_as_aware(row["first_answered_at"]),
                counted_for_mastery=bool(row["counted_for_mastery"]),
            )
            for row in rows
            if row["first_answered_at"] is not None
        )

    def add_project_submission(
        self,
        user_id: StableId,
        submission: ProjectSubmissionRef,
    ) -> None:
        """Record ``submission`` by ``user_id``; reject a reused ``stable_id``."""
        if self.get_project_submission(submission.stable_id) is not None:
            raise ValueError(
                f"project submission with stable_id {submission.stable_id!r} already exists"
            )
        try:
            with self._engine.begin() as connection:
                user_pk = _require_id(connection, m.user, user_id, "user")
                project_pk = _require_id(connection, m.project, submission.project_id, "project")
                connection.execute(
                    m.project_submission.insert().values(
                        stable_id=submission.stable_id,
                        user_id=user_pk,
                        project_id=project_pk,
                        review_outcome=submission.review_outcome,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError(f"project submission {submission.stable_id!r} already exists") from exc

    def get_project_submission(self, stable_id: StableId) -> ProjectSubmissionRef | None:
        """Return the project submission with ``stable_id``, or ``None`` when absent."""
        statement = self._submission_select().where(m.project_submission.c.stable_id == stable_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return None if row is None else _submission(row)

    def save_project_submission(self, submission: ProjectSubmissionRef) -> None:
        """Record the reviewer's outcome for the existing ``submission`` (``None`` = pending)."""
        with self._engine.begin() as connection:
            result = connection.execute(
                m.project_submission.update()
                .where(m.project_submission.c.stable_id == submission.stable_id)
                .values(review_outcome=submission.review_outcome)
            )
        if result.rowcount == 0:
            raise ValueError(
                f"project submission with stable_id {submission.stable_id!r} does not exist"
            )

    def list_project_submissions(
        self,
        user_id: StableId,
    ) -> tuple[ProjectSubmissionRef, ...]:
        """Return every project submission by ``user_id``, ordered by ``stable_id``."""
        statement = (
            self._submission_select()
            .where(m.user.c.stable_id == user_id)
            .order_by(m.project_submission.c.stable_id)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(_submission(row) for row in rows)

    # -- internals ----------------------------------------------------------

    def _fetch_attempt(self, condition: Any) -> QuizAttempt | None:
        with self._engine.connect() as connection:
            row = connection.execute(self._attempt_select().where(condition)).mappings().first()
        return None if row is None else _attempt(row)

    @staticmethod
    def _attempt_select() -> Any:
        return select(
            m.quiz_attempt.c.stable_id,
            m.quiz_attempt.c.score,
            m.quiz_attempt.c.is_best,
            m.quiz_attempt.c.attempted_at,
            m.quiz_attempt.c.content_version,
            m.quiz.c.stable_id.label("quiz_stable_id"),
        ).select_from(
            m.quiz_attempt.join(m.quiz, m.quiz_attempt.c.quiz_id == m.quiz.c.id).join(
                m.user, m.quiz_attempt.c.user_id == m.user.c.id
            )
        )

    @staticmethod
    def _submission_select() -> Any:
        return select(
            m.project_submission.c.stable_id,
            m.project_submission.c.review_outcome,
            m.project.c.stable_id.label("project_stable_id"),
        ).select_from(
            m.project_submission.join(
                m.project, m.project_submission.c.project_id == m.project.c.id
            ).join(m.user, m.project_submission.c.user_id == m.user.c.id)
        )


def _attempt(row: Any) -> QuizAttempt:
    return QuizAttempt(
        stable_id=row["stable_id"],
        quiz_id=row["quiz_stable_id"],
        score=int(row["score"] or 0),
        is_best=bool(row["is_best"]),
        attempted_at=_as_aware(row["attempted_at"]),
        content_version=row["content_version"] or "",
    )


def _submission(row: Any) -> ProjectSubmissionRef:
    return ProjectSubmissionRef(
        stable_id=row["stable_id"],
        project_id=row["project_stable_id"],
        review_outcome=row["review_outcome"],
    )


# ---------------------------------------------------------------------------
# Lessons
# ---------------------------------------------------------------------------

#: Supplies the per-locale ``(titles, bodies)`` of a lesson. D3 stores no MDX in the
#: ``lesson`` row (bodies live in the content package), so the text is injected.
LessonContentSource = Callable[[StableId], tuple[Mapping[str, str], Mapping[str, str]]]


class Postgres_LessonRepository(_Base, LessonRepository):
    """A read-only :class:`LessonRepository`: structure from D3, text from a source.

    The module, quiz link and ``content_version`` come from the ``lesson`` /
    ``quiz`` / ``content_provenance`` rows (``stable_id``-indexed). The per-locale
    titles and MDX bodies come from the optional ``content_source``; without one a
    lesson has no bodies (Req 17.3 fallback then has nothing to serve).
    """

    def __init__(
        self, engine: Engine | str, content_source: LessonContentSource | None = None
    ) -> None:
        """Create the repository, optionally with the lesson text ``content_source``."""
        super().__init__(engine)
        self._content_source = content_source

    def get_lesson(self, stable_id: StableId) -> LessonRecord | None:
        """Return the lesson with ``stable_id``, or ``None`` when absent."""
        statement = (
            select(
                m.module.c.stable_id.label("module_stable_id"),
                m.content_provenance.c.content_version,
                select(m.quiz.c.stable_id)
                .where(m.quiz.c.lesson_id == m.lesson.c.id)
                .order_by(m.quiz.c.id)
                .limit(1)
                .scalar_subquery()
                .label("quiz_stable_id"),
            )
            .select_from(
                m.lesson.join(m.module, m.lesson.c.module_id == m.module.c.id).join(
                    m.content_provenance, m.lesson.c.provenance_id == m.content_provenance.c.id
                )
            )
            .where(m.lesson.c.stable_id == stable_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        titles, bodies = self._content_source(stable_id) if self._content_source else ({}, {})
        return LessonRecord(
            stable_id=stable_id,
            module_id=row["module_stable_id"],
            titles=dict(titles),
            bodies=dict(bodies),
            quiz_id=row["quiz_stable_id"],
            content_version=row["content_version"],
        )


# ---------------------------------------------------------------------------
# Assessments (placement / final assessment; interface only, Req 21.1)
# ---------------------------------------------------------------------------


class Postgres_AssessmentRepository(_Base, AssessmentRepository):
    """Persistence of ``placement`` and ``final_assessment`` rows (D3 section 12).

    One row per learner x track, addressed by the learner's and track's
    ``stable_id``. Grading is not implemented in this phase (Req 21.2).
    """

    def get_placement(self, user_id: StableId, track_id: StableId) -> AssessmentRecord | None:
        """Return the learner's placement for the track, or ``None``."""
        return self._get(m.placement, user_id, track_id)

    def save_placement(self, record: AssessmentRecord) -> None:
        """Create or update the learner's placement for the track."""
        self._save(m.placement, record)

    def get_final_assessment(
        self, user_id: StableId, track_id: StableId
    ) -> AssessmentRecord | None:
        """Return the learner's final assessment for the track, or ``None``."""
        return self._get(m.final_assessment, user_id, track_id)

    def save_final_assessment(self, record: AssessmentRecord) -> None:
        """Create or update the learner's final assessment for the track."""
        self._save(m.final_assessment, record)

    def _get(self, table: Any, user_id: StableId, track_id: StableId) -> AssessmentRecord | None:
        statement = (
            select(table.c.status)
            .select_from(
                table.join(m.user, table.c.user_id == m.user.c.id).join(
                    m.track, table.c.track_id == m.track.c.id
                )
            )
            .where(m.user.c.stable_id == user_id, m.track.c.stable_id == track_id)
        )
        with self._engine.connect() as connection:
            status = connection.execute(statement).scalar_one_or_none()
        if status is None:
            return None
        return AssessmentRecord(user_id=user_id, track_id=track_id, status=AssessmentStatus(status))

    def _save(self, table: Any, record: AssessmentRecord) -> None:
        with self._engine.begin() as connection:
            user = _require_id(connection, m.user, record.user_id, "user")
            track = _require_id(connection, m.track, record.track_id, "track")
            updated = connection.execute(
                update(table)
                .where(table.c.user_id == user, table.c.track_id == track)
                .values(status=record.status.value)
            )
            if updated.rowcount == 0:
                connection.execute(
                    insert(table).values(user_id=user, track_id=track, status=record.status.value)
                )
