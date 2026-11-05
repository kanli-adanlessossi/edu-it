"""The PostgreSQL lab repositories (hands-on-labs task 5.2).

- :class:`Postgres_LabSessionRepository` ->
  :class:`~edu_it_domain.ports.lab_sessions.LabSessionRepository` (``lab_session``).
- :class:`Postgres_GradingRunRepository` ->
  :class:`~edu_it_domain.ports.lab_sessions.GradingRunRepository` (``lab_run`` and
  ``lab_task_completion``).

Conventions (same as the learning adapters): stable ids only at the boundary;
SQLAlchemy Core, one short transaction per operation; database-agnostic so unit
tests run on SQLite while production uses PostgreSQL.

- **Compare-and-set.** Every status change is a single ``UPDATE ... WHERE status IN
  (expected)`` and the winner is the statement with ``rowcount == 1``. The grading
  rate limit is part of that same statement, so concurrent requests cannot both win.
- **One Active_Session per learner** is enforced by the partial unique index
  ``uq_lab_session_active_user``; a violation maps to ``ActiveSessionExists``.
- **Keyset pagination** over ``(started_at, stable_id)``, never an offset.
- **Results** are stored as a JSON array in ``lab_run.results``; only ids, outcome and
  feedback key are persisted (Req 11.4). ``append_result`` locks the row
  (``FOR UPDATE`` on PostgreSQL) and is idempotent per check id.
- ``lab_session.created_at`` of the domain model is the ``started_at`` column.
  ``lab_task_completion`` has no user or instant column: the user is the run's
  session owner and ``completed_at`` is the run's ``graded_at`` (else ``accepted_at``).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Any

from edu_it_domain.labs.models import (
    ACTIVE_SESSION_STATUSES,
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
    StableId,
    TaskCompletion,
)
from edu_it_domain.ports.lab_sessions import (
    ActiveSessionExists,
)
from edu_it_domain.ports.pagination import Keyset
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence._ordering import code_point_order
from edu_it_infrastructure.persistence.learning_repositories import _Base, _internal_id
from edu_it_infrastructure.persistence.repositories import _as_aware

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_GradingRunRepository", "Postgres_LabSessionRepository"]

_TERMINAL_VALUES = tuple(
    s.value
    for s in (
        SessionStatus.ENDED,
        SessionStatus.EXPIRED,
        SessionStatus.IDLE_EXPIRED,
        SessionStatus.FAILED,
        SessionStatus.ENDED_BY_INFRASTRUCTURE,
    )
)

# Domain field name -> column name, for ``transition(**fields)``.
_FIELD_COLUMNS: dict[str, str] = {
    "created_at": "started_at",
    "ttl_at": "ttl_at",
    "sandbox_ref": "sandbox_ref",
    "last_activity_at": "last_activity_at",
    "last_grading_accepted_at": "last_grading_accepted_at",
    "ended_at": "ended_at",
    "provision_attempts": "provision_attempts",
    "next_provision_at": "next_provision_at",
    "terminal_epoch": "terminal_epoch",
    "capacity_attempts": "capacity_attempts",
}


def _tolerant_status(value: str) -> SessionStatus:
    """Read a stored status, tolerating a value this release does not know.

    A rolling upgrade or a rollback can leave rows written by another release (for
    example ``ended-by-infrastructure`` read by the previous release). An unknown value
    reads as the terminal ``failed`` status: inactive, so it never holds the learner's
    active-session slot and never selects a sandbox action (Req 21.6).
    """
    try:
        return SessionStatus(value)
    except ValueError:
        return SessionStatus.FAILED


def _opt(instant: datetime | None) -> datetime | None:
    return None if instant is None else _as_aware(instant)


class Postgres_LabSessionRepository(_Base):
    """A :class:`LabSessionRepository` over the ``lab_session`` table."""

    def _select(self) -> Any:
        return (
            select(
                m.lab_session,
                m.user.c.stable_id.label("user_sid"),
                m.lab.c.stable_id.label("lab_sid"),
            )
            .join(m.user, m.user.c.id == m.lab_session.c.user_id)
            .join(m.lab, m.lab.c.id == m.lab_session.c.lab_id)
        )

    @staticmethod
    def _to_domain(row: Any) -> LabSession:
        return LabSession(
            stable_id=row.stable_id,
            user_id=row.user_sid,
            lab_id=row.lab_sid,
            status=_tolerant_status(row.status),
            created_at=_as_aware(row.started_at),
            ttl_at=_as_aware(row.ttl_at),
            sandbox_ref=row.sandbox_ref,
            last_activity_at=_opt(row.last_activity_at),
            last_grading_accepted_at=_opt(row.last_grading_accepted_at),
            ended_at=_opt(row.ended_at),
            provision_attempts=row.provision_attempts,
            next_provision_at=_opt(row.next_provision_at),
            terminal_epoch=row.terminal_epoch,
            capacity_attempts=row.capacity_attempts,
        )

    def _one(self, where: Any) -> LabSession | None:
        with self._engine.begin() as connection:
            row = connection.execute(self._select().where(where)).first()
        return None if row is None else self._to_domain(row)

    def add_if_no_active(self, session: LabSession) -> None:
        with self._engine.begin() as connection:
            user_pk = _internal_id(connection, m.user, session.user_id)
            lab_pk = _internal_id(connection, m.lab, session.lab_id)
            if user_pk is None or lab_pk is None:
                raise LookupError("unknown user or lab")
            active = connection.execute(
                select(m.lab_session.c.id).where(
                    m.lab_session.c.user_id == user_pk,
                    m.lab_session.c.status.in_([s.value for s in ACTIVE_SESSION_STATUSES]),
                )
            ).first()
            if active is not None:
                raise ActiveSessionExists(session.user_id)
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(m.lab_session).values(
                            stable_id=session.stable_id,
                            user_id=user_pk,
                            lab_id=lab_pk,
                            started_at=session.created_at,
                            ttl_at=session.ttl_at,
                            status=session.status.value,
                            sandbox_ref=session.sandbox_ref,
                            last_activity_at=session.last_activity_at,
                            last_grading_accepted_at=session.last_grading_accepted_at,
                            ended_at=session.ended_at,
                            provision_attempts=session.provision_attempts,
                            next_provision_at=session.next_provision_at,
                            terminal_epoch=session.terminal_epoch,
                        )
                    )
            except IntegrityError as error:
                # Lost the race to the partial unique index, or the id exists.
                if self._exists(connection, session.stable_id):
                    raise ValueError(f"session exists: {session.stable_id}") from error
                raise ActiveSessionExists(session.user_id) from error

    @staticmethod
    def _exists(connection: Connection, stable_id: StableId) -> bool:
        return _internal_id(connection, m.lab_session, stable_id) is not None

    def get(self, session_id: StableId) -> LabSession | None:
        return self._one(m.lab_session.c.stable_id == session_id)

    def get_for_owner(self, session_id: StableId, user_id: StableId) -> LabSession | None:
        return self._one(
            and_(m.lab_session.c.stable_id == session_id, m.user.c.stable_id == user_id)
        )

    def get_active_for_user(self, user_id: StableId) -> LabSession | None:
        return self._one(
            and_(
                m.user.c.stable_id == user_id,
                m.lab_session.c.status.in_([s.value for s in ACTIVE_SESSION_STATUSES]),
            )
        )

    def transition(
        self,
        session_id: StableId,
        *,
        expected: Collection[SessionStatus],
        to: SessionStatus,
        **fields: object,
    ) -> bool:
        unknown = set(fields) - set(_FIELD_COLUMNS)
        if unknown:
            raise ValueError(f"unknown lab session fields: {sorted(unknown)}")
        values = {_FIELD_COLUMNS[k]: v for k, v in fields.items()}
        with self._engine.begin() as connection:
            result = connection.execute(
                update(m.lab_session)
                .where(
                    m.lab_session.c.stable_id == session_id,
                    m.lab_session.c.status.in_([s.value for s in expected]),
                )
                .values(status=to.value, **values)
            )
            return result.rowcount == 1

    def try_accept_grading(
        self, session_id: StableId, *, now: datetime, min_interval: timedelta
    ) -> bool:
        cutoff = now - min_interval
        with self._engine.begin() as connection:
            result = connection.execute(
                update(m.lab_session)
                .where(
                    m.lab_session.c.stable_id == session_id,
                    m.lab_session.c.status == SessionStatus.READY.value,
                    or_(
                        m.lab_session.c.last_grading_accepted_at.is_(None),
                        m.lab_session.c.last_grading_accepted_at < cutoff,
                    ),
                )
                .values(status=SessionStatus.GRADING.value, last_grading_accepted_at=now)
            )
            return result.rowcount == 1

    def claim_provision_attempt(
        self, session_id: StableId, *, expected_attempts: int, now: datetime
    ) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(m.lab_session)
                .where(
                    m.lab_session.c.stable_id == session_id,
                    m.lab_session.c.status == SessionStatus.PROVISIONING.value,
                    m.lab_session.c.provision_attempts == expected_attempts,
                )
                .values(provision_attempts=expected_attempts + 1)
            )
            return result.rowcount == 1

    def touch_activity(self, session_id: StableId, at: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(m.lab_session)
                .where(
                    m.lab_session.c.stable_id == session_id,
                    or_(
                        m.lab_session.c.last_activity_at.is_(None),
                        m.lab_session.c.last_activity_at < at,
                    ),
                )
                .values(last_activity_at=at)
            )

    def bump_terminal_epoch(self, session_id: StableId) -> int | None:
        with self._engine.begin() as connection:
            return connection.execute(
                update(m.lab_session)
                .where(m.lab_session.c.stable_id == session_id)
                .values(terminal_epoch=m.lab_session.c.terminal_epoch + 1)
                .returning(m.lab_session.c.terminal_epoch)
            ).scalar_one_or_none()

    def count_started_since(self, user_id: StableId, since: datetime) -> int:
        with self._engine.begin() as connection:
            return int(
                connection.execute(
                    select(func.count())
                    .select_from(m.lab_session.join(m.user, m.user.c.id == m.lab_session.c.user_id))
                    .where(m.user.c.stable_id == user_id, m.lab_session.c.started_at >= since)
                ).scalar_one()
            )

    def list_for_user_page(
        self, user_id: StableId, *, after: Keyset | None = None, limit: int
    ) -> tuple[LabSession, ...]:
        # Ties on the instant break by stable_id in code-point order, as the fake does.
        tie_break = code_point_order(m.lab_session.c.stable_id, self._engine)
        statement = self._select().where(m.user.c.stable_id == user_id)
        if after is not None:
            started_at, after_id = after
            statement = statement.where(
                or_(
                    m.lab_session.c.started_at > started_at,
                    and_(m.lab_session.c.started_at == started_at, tie_break > after_id),
                )
            )
        statement = statement.order_by(m.lab_session.c.started_at, tie_break).limit(limit)
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        return tuple(self._to_domain(r) for r in rows)

    def list_active(self) -> Sequence[LabSession]:
        statement = (
            self._select()
            .where(m.lab_session.c.status.in_([s.value for s in ACTIVE_SESSION_STATUSES]))
            .order_by(m.lab_session.c.started_at, m.lab_session.c.stable_id)
        )
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        return tuple(self._to_domain(r) for r in rows)

    def list_ended_before(self, cutoff: datetime) -> Sequence[LabSession]:
        statement = (
            self._select()
            .where(
                m.lab_session.c.status.in_(_TERMINAL_VALUES),
                m.lab_session.c.ended_at.is_not(None),
                m.lab_session.c.ended_at < cutoff,
            )
            .order_by(m.lab_session.c.ended_at, m.lab_session.c.stable_id)
        )
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        return tuple(self._to_domain(r) for r in rows)

    def delete_session_data(self, session_ids: Collection[StableId]) -> int:
        ids = list(session_ids)
        if not ids:
            return 0
        with self._engine.begin() as connection:
            pks: list[int] = list(
                connection.execute(
                    select(m.lab_session.c.id).where(m.lab_session.c.stable_id.in_(ids))
                ).scalars()
            )
            if not pks:
                return 0
            run_pks = select(m.lab_run.c.id).where(m.lab_run.c.lab_session_id.in_(pks))
            connection.execute(
                delete(m.lab_task_completion).where(m.lab_task_completion.c.lab_run_id.in_(run_pks))
            )
            connection.execute(delete(m.lab_run).where(m.lab_run.c.lab_session_id.in_(pks)))
            connection.execute(delete(m.lab_session).where(m.lab_session.c.id.in_(pks)))
            return len(pks)


def _encode_results(results: Sequence[CheckResult]) -> str:
    return json.dumps(
        [
            {
                "check_id": r.check_id,
                "task_id": r.task_id,
                "outcome": r.outcome.value,
                "outcome_code": r.outcome_code,
                "feedback_key": r.feedback_key,
            }
            for r in results
        ]
    )


def _decode_results(raw: str | None) -> tuple[CheckResult, ...]:
    if not raw:
        return ()
    return tuple(
        CheckResult(
            check_id=d["check_id"],
            task_id=d["task_id"],
            outcome=CheckOutcome(d["outcome"]),
            outcome_code=d["outcome_code"],
            feedback_key=d["feedback_key"],
        )
        for d in json.loads(raw)
    )


class Postgres_GradingRunRepository(_Base):
    """A :class:`GradingRunRepository` over ``lab_run`` and ``lab_task_completion``."""

    def _select(self) -> Any:
        return select(m.lab_run, m.lab_session.c.stable_id.label("session_sid")).join(
            m.lab_session, m.lab_session.c.id == m.lab_run.c.lab_session_id
        )

    @staticmethod
    def _to_domain(row: Any) -> GradingRun:
        return GradingRun(
            stable_id=row.stable_id,
            session_id=row.session_sid,
            status=RunStatus(row.status),
            accepted_at=_as_aware(row.accepted_at),
            deadline_at=_as_aware(row.deadline_at),
            started_at=_opt(row.started_at),
            graded_at=_opt(row.graded_at),
            results=_decode_results(row.results),
        )

    def add(self, run: GradingRun) -> None:
        with self._engine.begin() as connection:
            session_pk = _internal_id(connection, m.lab_session, run.session_id)
            if session_pk is None:
                raise LookupError(f"unknown session: {run.session_id}")
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(m.lab_run).values(
                            stable_id=run.stable_id,
                            lab_session_id=session_pk,
                            status=run.status.value,
                            accepted_at=run.accepted_at,
                            deadline_at=run.deadline_at,
                            started_at=run.started_at,
                            graded_at=run.graded_at,
                            results=_encode_results(run.results),
                        )
                    )
            except IntegrityError as error:
                raise ValueError(f"run exists: {run.stable_id}") from error

    def get(self, run_id: StableId) -> GradingRun | None:
        with self._engine.begin() as connection:
            row = connection.execute(self._select().where(m.lab_run.c.stable_id == run_id)).first()
        return None if row is None else self._to_domain(row)

    def append_result(self, run_id: StableId, result: CheckResult) -> None:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(m.lab_run.c.id, m.lab_run.c.results)
                .where(m.lab_run.c.stable_id == run_id)
                .with_for_update()
            ).first()
            if row is None:
                raise KeyError(run_id)
            existing = _decode_results(row.results)
            if any(r.check_id == result.check_id for r in existing):
                return
            connection.execute(
                update(m.lab_run)
                .where(m.lab_run.c.id == row.id)
                .values(results=_encode_results((*existing, result)))
            )

    def finish(self, run_id: StableId, *, status: RunStatus, graded_at: datetime) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(m.lab_run)
                .where(
                    m.lab_run.c.stable_id == run_id,
                    m.lab_run.c.status == RunStatus.RUNNING.value,
                )
                .values(status=status.value, graded_at=graded_at)
            )
            return result.rowcount == 1

    def list_running(self) -> Sequence[GradingRun]:
        statement = (
            self._select()
            .where(m.lab_run.c.status == RunStatus.RUNNING.value)
            .order_by(m.lab_run.c.accepted_at, m.lab_run.c.stable_id)
        )
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        return tuple(self._to_domain(r) for r in rows)

    def first_pass_exists(self, user_id: StableId, task_id: StableId) -> bool:
        statement = (
            select(m.lab_task_completion.c.id)
            .select_from(
                m.lab_task_completion.join(
                    m.lab_run, m.lab_run.c.id == m.lab_task_completion.c.lab_run_id
                )
                .join(m.lab_session, m.lab_session.c.id == m.lab_run.c.lab_session_id)
                .join(m.user, m.user.c.id == m.lab_session.c.user_id)
                .join(m.lab_task, m.lab_task.c.id == m.lab_task_completion.c.lab_task_id)
            )
            .where(m.user.c.stable_id == user_id, m.lab_task.c.stable_id == task_id)
            .limit(1)
        )
        with self._engine.begin() as connection:
            return connection.execute(statement).first() is not None

    def add_task_completions(self, run_id: StableId, rows: Sequence[TaskCompletion]) -> None:
        with self._engine.begin() as connection:
            run_pk = _internal_id(connection, m.lab_run, run_id)
            if run_pk is None:
                raise LookupError(f"unknown run: {run_id}")
            for row in rows:
                if self._completion_exists(connection, run_pk, row.task_id):
                    continue
                task_pk = _internal_id(connection, m.lab_task, row.task_id)
                if task_pk is None:
                    raise LookupError(f"unknown task: {row.task_id}")
                connection.execute(
                    insert(m.lab_task_completion).values(
                        lab_run_id=run_pk,
                        lab_task_id=task_pk,
                        passed=True,
                        counted_for_mastery=row.counted_for_mastery,
                    )
                )

    @staticmethod
    def _completion_exists(connection: Connection, run_pk: int, task_id: StableId) -> bool:
        """Idempotent per ``(run, task)``: repeat passes in later runs are still recorded."""
        statement = (
            select(m.lab_task_completion.c.id)
            .select_from(
                m.lab_task_completion.join(
                    m.lab_task, m.lab_task.c.id == m.lab_task_completion.c.lab_task_id
                )
            )
            .where(
                m.lab_task_completion.c.lab_run_id == run_pk,
                m.lab_task.c.stable_id == task_id,
            )
            .limit(1)
        )
        return connection.execute(statement).first() is not None

    def list_task_completions(self, user_id: StableId) -> Sequence[TaskCompletion]:
        """All of a learner's completions (instant derived from the run; see module doc)."""
        statement = (
            select(
                m.lab_task.c.stable_id.label("task_sid"),
                m.lab_run.c.stable_id.label("run_sid"),
                m.lab_task_completion.c.counted_for_mastery,
                m.lab_run.c.graded_at,
                m.lab_run.c.accepted_at,
            )
            .select_from(
                m.lab_task_completion.join(
                    m.lab_run, m.lab_run.c.id == m.lab_task_completion.c.lab_run_id
                )
                .join(m.lab_session, m.lab_session.c.id == m.lab_run.c.lab_session_id)
                .join(m.user, m.user.c.id == m.lab_session.c.user_id)
                .join(m.lab_task, m.lab_task.c.id == m.lab_task_completion.c.lab_task_id)
            )
            .where(m.user.c.stable_id == user_id)
            .order_by(m.lab_task_completion.c.id)
        )
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        found = [
            TaskCompletion(
                user_id=user_id,
                task_id=r.task_sid,
                run_id=r.run_sid,
                completed_at=_as_aware(r.graded_at or r.accepted_at),
                counted_for_mastery=bool(r.counted_for_mastery),
            )
            for r in rows
        ]
        return tuple(sorted(found, key=lambda c: (c.completed_at, c.task_id)))

    def completions_for_run(self, run_id: StableId) -> tuple[TaskCompletion, ...]:
        """Read back a run's completions (user and instant derived; see module doc)."""
        statement = (
            select(
                m.user.c.stable_id.label("user_sid"),
                m.lab_task.c.stable_id.label("task_sid"),
                m.lab_task_completion.c.counted_for_mastery,
                m.lab_run.c.graded_at,
                m.lab_run.c.accepted_at,
            )
            .select_from(
                m.lab_task_completion.join(
                    m.lab_run, m.lab_run.c.id == m.lab_task_completion.c.lab_run_id
                )
                .join(m.lab_session, m.lab_session.c.id == m.lab_run.c.lab_session_id)
                .join(m.user, m.user.c.id == m.lab_session.c.user_id)
                .join(m.lab_task, m.lab_task.c.id == m.lab_task_completion.c.lab_task_id)
            )
            .where(m.lab_run.c.stable_id == run_id)
            .order_by(m.lab_task_completion.c.id)
        )
        with self._engine.begin() as connection:
            rows = connection.execute(statement).all()
        return tuple(
            TaskCompletion(
                user_id=r.user_sid,
                task_id=r.task_sid,
                run_id=run_id,
                completed_at=_as_aware(r.graded_at or r.accepted_at),
                counted_for_mastery=bool(r.counted_for_mastery),
            )
            for r in rows
        )
