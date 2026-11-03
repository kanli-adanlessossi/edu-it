"""The PostgreSQL ``VariantRepository`` and ``ReviewTaskRepository`` (ai-content task 12.2).

Over ``variant``, ``variant_revision`` and ``review_queue_item`` (revision
``0005_ai_content``). ``ReviewTaskRepository.decide`` runs the task update, the
variant write and the revision write in one transaction: any failure rolls all
three back. Stable ids only: internal keys are resolved inside each operation and
never returned. Instants are stored as aware UTC. Database-agnostic SQLAlchemy
Core, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

from edu_it_domain.ai.lifecycle import (
    SERVABLE_STATES,
    InvalidTransitionError,
    VariantState,
    can_transition,
)
from edu_it_domain.ports.pagination import Page
from edu_it_domain.ports.review_tasks import (
    Decision,
    NewReviewTask,
    ReviewTask,
    ReviewTaskAlreadyDecidedError,
    TargetKind,
    TaskKind,
    TaskState,
)
from edu_it_domain.ports.variants import (
    NewRevision,
    NewVariant,
    RevisionState,
    Variant,
    VariantRevision,
)
from sqlalchemy import and_, case, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence._ordering import code_point_order
from edu_it_infrastructure.persistence.learning_repositories import _Base, _require_id
from edu_it_infrastructure.persistence.repositories import _as_aware, _as_aware_optional

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from edu_it_domain.ports.pagination import Keyset
    from sqlalchemy.engine import Connection

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_ReviewTaskRepository", "Postgres_VariantRepository"]

_SERVABLE = tuple(s.value for s in SERVABLE_STATES)


def _variant_select() -> Any:
    v, job = m.variant, m.generation_job
    return select(
        v.c.stable_id,
        v.c.parent_question_stable_id,
        v.c.track_stable_id,
        v.c.lifecycle_state,
        v.c.created_at,
        v.c.approved_content_version,
        v.c.withdrawn_reason,
        job.c.stable_id.label("job_stable_id"),
    ).select_from(v.outerjoin(job, job.c.id == v.c.generation_job_id))


def _to_variant(row: Any) -> Variant:
    return Variant(
        stable_id=row.stable_id,
        parent_question_stable_id=row.parent_question_stable_id,
        track_stable_id=row.track_stable_id,
        state=VariantState(row.lifecycle_state),
        created_at=_as_aware(row.created_at),
        approved_content_version=row.approved_content_version,
        withdrawn_reason=row.withdrawn_reason,
        generation_job_stable_id=row.job_stable_id,
    )


def _revision_select() -> Any:
    r, v, u = m.variant_revision, m.variant, m.user
    return select(
        v.c.stable_id.label("variant_stable_id"),
        r.c.content_version,
        r.c.body_json,
        r.c.state,
        r.c.created_at,
        r.c.origin,
        r.c.model,
        r.c.prompt_id,
        r.c.prompt_version,
        r.c.generation_date,
        u.c.stable_id.label("reviewer_stable_id"),
        r.c.reviewed_at,
        r.c.findings_json,
    ).select_from(r.join(v, v.c.id == r.c.variant_id).outerjoin(u, u.c.id == r.c.reviewed_by))


def _to_revision(row: Any) -> VariantRevision:
    return VariantRevision(
        variant_stable_id=row.variant_stable_id,
        content_version=row.content_version,
        body_json=row.body_json,
        state=RevisionState(row.state),
        created_at=_as_aware(row.created_at),
        origin=row.origin,
        model=row.model,
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        generation_date=_as_aware_optional(row.generation_date),
        reviewed_by=row.reviewer_stable_id,
        reviewed_at=_as_aware_optional(row.reviewed_at),
        findings_json=row.findings_json,
    )


def _save_variant(connection: Connection, variant: Variant) -> None:
    """Store ``variant`` on ``connection``; the state change must be a legal transition."""
    table = m.variant
    row = connection.execute(
        select(table.c.lifecycle_state).where(table.c.stable_id == variant.stable_id)
    ).first()
    if row is None:
        raise ValueError(f"unknown variant {variant.stable_id!r}")
    stored = VariantState(row.lifecycle_state)
    if variant.state != stored and not can_transition(stored, variant.state):
        raise InvalidTransitionError(stored, variant.state)
    result = connection.execute(
        update(table)
        .where(table.c.stable_id == variant.stable_id, table.c.lifecycle_state == stored.value)
        .values(
            lifecycle_state=variant.state.value,
            approved_content_version=variant.approved_content_version,
            withdrawn_reason=variant.withdrawn_reason,
        )
    )
    if result.rowcount != 1:  # changed by a concurrent writer since it was read
        raise ValueError(f"variant {variant.stable_id!r} changed concurrently")


class Postgres_VariantRepository(_Base):
    """A :class:`VariantRepository` over ``variant`` and ``variant_revision``."""

    def __init__(self, engine: Any, clock: Any) -> None:
        super().__init__(engine)
        self._clock = clock

    def _now(self) -> datetime:
        return _as_aware(self._clock.now())

    def add(self, variant: NewVariant) -> None:
        try:
            with self._engine.begin() as connection:
                question_id = _require_id(
                    connection, m.question, variant.parent_question_stable_id, "parent question"
                )
                job_id = (
                    None
                    if variant.generation_job_stable_id is None
                    else _require_id(
                        connection, m.generation_job, variant.generation_job_stable_id, "job"
                    )
                )
                connection.execute(
                    insert(m.variant).values(
                        stable_id=variant.stable_id,
                        question_id=question_id,
                        generation_job_id=job_id,
                        lifecycle_state=variant.state.value,
                        parent_question_stable_id=variant.parent_question_stable_id,
                        track_stable_id=variant.track_stable_id,
                        created_at=self._now(),
                    )
                )
        except IntegrityError as error:
            raise ValueError(f"variant exists: {variant.stable_id}") from error

    def get(self, stable_id: str) -> Variant | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                _variant_select().where(m.variant.c.stable_id == stable_id)
            ).first()
        return None if row is None else _to_variant(row)

    def save(self, variant: Variant) -> None:
        with self._engine.begin() as connection:
            _save_variant(connection, variant)

    def count_approved(self, parent_question_stable_id: str) -> int:
        v = m.variant
        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count()).where(
                        v.c.parent_question_stable_id == parent_question_stable_id,
                        v.c.lifecycle_state.in_(_SERVABLE),
                    )
                ).scalar_one()
            )

    def parents_below(self, threshold: int) -> Sequence[str]:
        v = m.variant
        servable = func.coalesce(
            func.sum(case((v.c.lifecycle_state.in_(_SERVABLE), 1), else_=0)), 0
        )
        statement = (
            select(v.c.parent_question_stable_id)
            .where(v.c.parent_question_stable_id.is_not(None))
            .group_by(v.c.parent_question_stable_id)
            .having(servable < threshold)
            .order_by(code_point_order(v.c.parent_question_stable_id, self._engine))
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).all()
        return [str(row[0]) for row in rows]

    def list_servable(self, parent_question_stable_id: str) -> Sequence[VariantRevision]:
        v, r = m.variant, m.variant_revision
        statement = (
            _revision_select()
            .where(
                v.c.parent_question_stable_id == parent_question_stable_id,
                v.c.lifecycle_state.in_(_SERVABLE),
                r.c.content_version == v.c.approved_content_version,
            )
            .order_by(code_point_order(v.c.stable_id, self._engine))
        )
        with self._engine.connect() as connection:
            return [_to_revision(row) for row in connection.execute(statement)]

    def add_revision(self, revision: NewRevision) -> None:
        try:
            with self._engine.begin() as connection:
                variant_id = _require_id(
                    connection, m.variant, revision.variant_stable_id, "variant"
                )
                reviewer_id = (
                    None
                    if revision.reviewed_by is None
                    else _require_id(connection, m.user, revision.reviewed_by, "reviewer")
                )
                connection.execute(
                    insert(m.variant_revision).values(
                        variant_id=variant_id,
                        content_version=revision.content_version,
                        body_json=revision.body_json,
                        state=revision.state.value,
                        origin=revision.origin,
                        model=revision.model,
                        prompt_id=revision.prompt_id,
                        prompt_version=revision.prompt_version,
                        generation_date=_as_aware_optional(revision.generation_date),
                        reviewed_by=reviewer_id,
                        reviewed_at=(
                            None
                            if revision.reviewed_at is None
                            else _as_aware(revision.reviewed_at)
                        ),
                        findings_json=revision.findings_json,
                        created_at=self._now(),
                    )
                )
        except IntegrityError as error:
            raise ValueError(
                f"revision exists: {revision.variant_stable_id}/{revision.content_version}"
            ) from error

    def get_revision(self, variant_stable_id: str, content_version: str) -> VariantRevision | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                _revision_select().where(
                    m.variant.c.stable_id == variant_stable_id,
                    m.variant_revision.c.content_version == content_version,
                )
            ).first()
        return None if row is None else _to_revision(row)


def _task_select() -> Any:
    q, u = m.review_queue_item, m.user
    return select(
        q.c.stable_id,
        q.c.kind,
        q.c.target_kind,
        q.c.target_stable_id,
        q.c.track_stable_id,
        q.c.state,
        q.c.created_at,
        q.c.revision_content_version,
        q.c.reason,
        q.c.comment,
        u.c.stable_id.label("reviewer_stable_id"),
        q.c.decided_at,
    ).select_from(q.outerjoin(u, u.c.id == q.c.reviewer_id))


def _to_task(row: Any) -> ReviewTask:
    return ReviewTask(
        stable_id=row.stable_id,
        kind=TaskKind(row.kind),
        target_kind=TargetKind(row.target_kind),
        target_stable_id=row.target_stable_id,
        track_stable_id=row.track_stable_id,
        state=TaskState(row.state),
        created_at=_as_aware(row.created_at),
        revision_content_version=row.revision_content_version,
        reason=row.reason,
        comment=row.comment,
        reviewer_stable_id=row.reviewer_stable_id,
        decided_at=_as_aware_optional(row.decided_at),
    )


class Postgres_ReviewTaskRepository(_Base):
    """A :class:`ReviewTaskRepository` over ``review_queue_item``."""

    def __init__(self, engine: Any, clock: Any) -> None:
        super().__init__(engine)
        self._clock = clock

    def _now(self) -> datetime:
        return _as_aware(self._clock.now())

    def add(self, task: NewReviewTask) -> ReviewTask:
        q = m.review_queue_item
        version = task.revision_content_version
        try:
            with self._engine.begin() as connection:
                same_version = (
                    q.c.revision_content_version.is_(None)
                    if version is None
                    else q.c.revision_content_version == version
                )
                open_row = connection.execute(
                    _task_select().where(
                        q.c.state == TaskState.OPEN.value,
                        q.c.kind == task.kind.value,
                        q.c.target_kind == task.target_kind.value,
                        q.c.target_stable_id == task.target_stable_id,
                        same_version,
                    )
                ).first()
                if open_row is not None:
                    return _to_task(open_row)
                variant_id = (
                    _require_id(connection, m.variant, task.target_stable_id, "variant")
                    if task.target_kind is TargetKind.VARIANT
                    else None
                )
                connection.execute(
                    insert(q).values(
                        stable_id=task.stable_id,
                        variant_id=variant_id,
                        state=TaskState.OPEN.value,
                        kind=task.kind.value,
                        target_kind=task.target_kind.value,
                        target_stable_id=task.target_stable_id,
                        revision_content_version=version,
                        track_stable_id=task.track_stable_id,
                        reason=task.reason,
                        created_at=self._now(),
                    )
                )
                row = connection.execute(
                    _task_select().where(q.c.stable_id == task.stable_id)
                ).one()
        except IntegrityError as error:
            raise ValueError(f"review task exists: {task.stable_id}") from error
        return _to_task(row)

    def get(self, stable_id: str) -> ReviewTask | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                _task_select().where(m.review_queue_item.c.stable_id == stable_id)
            ).first()
        return None if row is None else _to_task(row)

    def decide(self, task: ReviewTask, decision: Decision) -> None:
        q = m.review_queue_item
        # One transaction: an exception anywhere below rolls back all three writes.
        with self._engine.begin() as connection:
            stored = connection.execute(
                select(
                    q.c.id,
                    q.c.state,
                    q.c.target_kind,
                    q.c.target_stable_id,
                    q.c.revision_content_version,
                ).where(q.c.stable_id == task.stable_id)
            ).first()
            if stored is None:
                raise ValueError(f"unknown review task {task.stable_id!r}")
            if stored.state != TaskState.OPEN.value:
                raise ReviewTaskAlreadyDecidedError(task.stable_id)
            reviewer_id = _require_id(connection, m.user, decision.reviewer_stable_id, "reviewer")
            is_variant = stored.target_kind == TargetKind.VARIANT.value
            if decision.variant is not None:
                if not is_variant or decision.variant.stable_id != stored.target_stable_id:
                    raise ValueError("decision variant is not the task target")
                _save_variant(connection, decision.variant)
            decided_at = _as_aware(decision.decided_at)
            if decision.revision_state is not None:
                if not is_variant or stored.revision_content_version is None:
                    raise ValueError("task names no revision")
                variant_id = _require_id(connection, m.variant, stored.target_stable_id, "variant")
                r = m.variant_revision
                result = connection.execute(
                    update(r)
                    .where(
                        r.c.variant_id == variant_id,
                        r.c.content_version == stored.revision_content_version,
                    )
                    .values(
                        state=decision.revision_state.value,
                        reviewed_by=reviewer_id,
                        reviewed_at=decided_at,
                    )
                )
                if result.rowcount != 1:
                    raise ValueError(f"unknown revision {stored.revision_content_version!r}")
            result = connection.execute(
                update(q)
                .where(q.c.id == stored.id, q.c.state == TaskState.OPEN.value)
                .values(
                    state=decision.outcome.task_state.value,
                    reviewer_id=reviewer_id,
                    comment=decision.comment,
                    decided_at=decided_at,
                )
            )
            if result.rowcount != 1:  # decided by a concurrent writer
                raise ReviewTaskAlreadyDecidedError(task.stable_id)

    def page(self, tracks: Collection[str], after: Keyset | None, limit: int) -> Page[ReviewTask]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if not tracks:
            return Page(())
        q = m.review_queue_item
        stable_id = code_point_order(q.c.stable_id, self._engine)
        statement = (
            _task_select()
            .where(q.c.state == TaskState.OPEN.value, q.c.track_stable_id.in_(sorted(tracks)))
            .order_by(q.c.created_at, stable_id)
            .limit(limit + 1)
        )
        if after is not None:
            moment = _as_aware(after[0])
            statement = statement.where(
                or_(
                    q.c.created_at > moment,
                    and_(q.c.created_at == moment, stable_id > after[1]),
                )
            )
        with self._engine.connect() as connection:
            tasks = [_to_task(row) for row in connection.execute(statement)]
        items = tuple(tasks[:limit])
        next_after = (items[-1].created_at, items[-1].stable_id) if len(tasks) > limit else None
        return Page(items, next_after)
