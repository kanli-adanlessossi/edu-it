"""The PostgreSQL ``ProblemReportRepository`` (ai-content task 20.1).

Over ``problem_report`` (revision ``0005_ai_content``). Learners are addressed by their
``stable_id`` at the port and by the internal key in the table. Database-agnostic
SQLAlchemy Core, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.problem_reports import ProblemReport
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.learning_repositories import _Base, _internal_id, _require_id
from edu_it_infrastructure.persistence.repositories import _as_aware

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Engine

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_ProblemReportRepository"]


class Postgres_ProblemReportRepository(_Base):
    """A :class:`ProblemReportRepository` over SQL."""

    def __init__(self, engine: Engine | str) -> None:
        super().__init__(engine)

    def add(self, report: ProblemReport) -> None:
        try:
            with self._engine.begin() as connection:
                user_id = _require_id(connection, m.user, report.user_id, "user")
                connection.execute(
                    insert(m.problem_report).values(
                        stable_id=report.stable_id,
                        user_id=user_id,
                        item_kind=report.key.kind.value,
                        item_stable_id=report.key.stable_id,
                        content_version=report.content_version,
                        category=report.category,
                        comment=report.comment,
                        created_at=_as_aware(report.created_at),
                    )
                )
        except IntegrityError as error:
            raise ValueError(f"duplicate problem report {report.stable_id!r}") from error

    def get(self, stable_id: str) -> ProblemReport | None:
        t = m.problem_report
        with self._engine.connect() as connection:
            row = connection.execute(
                select(
                    t.c.stable_id,
                    m.user.c.stable_id,
                    t.c.item_kind,
                    t.c.item_stable_id,
                    t.c.content_version,
                    t.c.category,
                    t.c.comment,
                    t.c.created_at,
                )
                .join(m.user, m.user.c.id == t.c.user_id)
                .where(t.c.stable_id == stable_id)
            ).first()
        if row is None:
            return None
        return ProblemReport(
            stable_id=str(row[0]),
            user_id=str(row[1]),
            key=ItemKey(ItemKind(row[2]), str(row[3])),
            content_version=str(row[4]),
            category=str(row[5]),
            comment=None if row[6] is None else str(row[6]),
            created_at=_as_aware(row[7]),
        )

    def reporters_for(self, key: ItemKey, content_version: str) -> frozenset[str]:
        t = m.problem_report
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(m.user.c.stable_id)
                .select_from(t.join(m.user, m.user.c.id == t.c.user_id))
                .where(
                    t.c.item_kind == key.kind.value,
                    t.c.item_stable_id == key.stable_id,
                    t.c.content_version == content_version,
                )
                .distinct()
            ).all()
        return frozenset(str(row[0]) for row in rows)

    def delete_created_before(self, cutoff: datetime) -> int:
        t = m.problem_report
        with self._engine.begin() as connection:
            result = connection.execute(delete(t).where(t.c.created_at < _as_aware(cutoff)))
            return int(result.rowcount)

    def delete_for_user(self, user_id: str) -> int:
        with self._engine.begin() as connection:
            internal = _internal_id(connection, m.user, user_id)
            if internal is None:
                return 0
            result = connection.execute(
                delete(m.problem_report).where(m.problem_report.c.user_id == internal)
            )
            return int(result.rowcount)
