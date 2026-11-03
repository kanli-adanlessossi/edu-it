"""The PostgreSQL ``GenerationLedger`` (ai-content task 3.2).

Over ``generation_job`` and ``budget_ledger`` (revision ``0005_ai_content``).
``record_attempt`` runs in one transaction: the job row is updated first (which
also takes its row lock on PostgreSQL, so concurrent attempts on one job get
distinct ``attempt_no`` values), then the ledger row is inserted. Instants are
stored as aware UTC. Database-agnostic, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any

from edu_it_domain.ports.generation_ledger import JobRef, JobStatus, NewJob
from edu_it_domain.ports.llm_provider import AttemptUsage
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.learning_repositories import _Base
from edu_it_infrastructure.persistence.repositories import _as_aware

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_GenerationLedger"]


class Postgres_GenerationLedger(_Base):
    """A :class:`GenerationLedger` over ``generation_job`` and ``budget_ledger``."""

    def __init__(self, engine: Any, clock: Any) -> None:
        super().__init__(engine)
        self._clock = clock

    def _now(self) -> datetime:
        return _as_aware(self._clock.now())

    def open_job(self, job: NewJob) -> JobRef:
        statement = insert(m.generation_job).values(
            stable_id=job.stable_id,
            status=JobStatus.RUNNING.value,
            tokens=0,
            cost_cents=0,
            kind=job.kind.value,
            track_stable_id=job.track_stable_id,
            stage=job.stage,
            unit_key=job.unit_key,
            prompt_id=job.prompt_id,
            prompt_version=job.prompt_version,
            cost_cap_cents=job.cost_cap_cents,
            branch=job.branch,
            started_at=self._now(),
        )
        try:
            with self._engine.begin() as connection:
                connection.execute(statement)
        except IntegrityError as error:
            raise ValueError(f"job exists: {job.stable_id}") from error
        return JobRef(job.stable_id)

    def record_attempt(self, job: JobRef, usage: AttemptUsage) -> None:
        tokens = usage.input_tokens + usage.output_tokens
        table = m.generation_job
        with self._engine.begin() as connection:
            result = connection.execute(
                update(table)
                .where(table.c.stable_id == job.stable_id)
                .values(
                    tokens=func.coalesce(table.c.tokens, 0) + tokens,
                    cost_cents=func.coalesce(table.c.cost_cents, 0) + usage.cost_cents,
                    model=usage.model,
                )
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown job {job.stable_id!r}")
            row = connection.execute(
                select(table.c.id, table.c.track_stable_id).where(
                    table.c.stable_id == job.stable_id
                )
            ).one()
            ledger = m.budget_ledger
            count = connection.execute(
                select(func.count()).select_from(ledger).where(ledger.c.generation_job_id == row.id)
            ).scalar_one()
            connection.execute(
                insert(ledger).values(
                    generation_job_id=row.id,
                    attempt_no=count + 1,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    tokens=tokens,
                    cost_cents=usage.cost_cents,
                    outcome=usage.outcome,
                    track_stable_id=row.track_stable_id,
                    recorded_at=self._now(),
                )
            )

    def close_job(self, job: JobRef, status: JobStatus) -> None:
        table = m.generation_job
        with self._engine.begin() as connection:
            result = connection.execute(
                update(table)
                .where(table.c.stable_id == job.stable_id)
                .values(status=status.value, finished_at=self._now())
            )
        if result.rowcount != 1:
            raise ValueError(f"unknown job {job.stable_id!r}")

    def _sum(self, *conditions: Any, join_job: Any = None) -> int:
        ledger = m.budget_ledger
        statement = select(func.coalesce(func.sum(ledger.c.cost_cents), 0))
        if join_job is not None:
            statement = statement.select_from(
                ledger.join(m.generation_job, m.generation_job.c.id == ledger.c.generation_job_id)
            )
        with self._engine.connect() as connection:
            return int(connection.execute(statement.where(*conditions)).scalar_one())

    def spent_today(self, day_start: datetime) -> int:
        return self._sum(m.budget_ledger.c.recorded_at >= _as_aware(day_start))

    def spent_for_track(self, track_stable_id: str) -> int:
        return self._sum(m.budget_ledger.c.track_stable_id == track_stable_id)

    def spent_for_job(self, job: JobRef) -> int:
        return self._sum(m.generation_job.c.stable_id == job.stable_id, join_job=True)
