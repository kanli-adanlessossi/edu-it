"""The PostgreSQL ``PathRecommendationRepository`` (ai-content task 22.2).

Over ``path_recommendation`` and ``advisor_audit_log`` (revision ``0005_ai_content``).
Learners are addressed by their ``stable_id`` at the port and by the internal key in the
tables. The recommendation and its audit entry are written in one transaction.
Database-agnostic SQLAlchemy Core, so unit tests run on SQLite.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.learning_repositories import _Base, _internal_id, _require_id
from edu_it_infrastructure.persistence.repositories import _as_aware, _as_aware_optional

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_PathRecommendationRepository"]

#: Stands in for ``created_at`` of a row that predates revision ``0005_ai_content``.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _dumps(ids: tuple[str, ...]) -> str:
    return json.dumps(list(ids), ensure_ascii=False)


def _loads(text: object) -> tuple[str, ...]:
    return tuple(str(item) for item in json.loads(str(text)))


class Postgres_PathRecommendationRepository(_Base):
    """A :class:`PathRecommendationRepository` over SQL."""

    def __init__(self, engine: Engine | str) -> None:
        super().__init__(engine)

    def record_run(self, recommendation: PathRecommendation, audit: AdvisorAuditEntry) -> None:
        try:
            with self._engine.begin() as connection:
                user_id = _require_id(connection, m.user, recommendation.user_id, "user")
                audit_user_id = (
                    user_id
                    if audit.user_id == recommendation.user_id
                    else _require_id(connection, m.user, audit.user_id, "user")
                )
                connection.execute(
                    insert(m.path_recommendation).values(
                        stable_id=recommendation.stable_id,
                        user_id=user_id,
                        source=recommendation.source,
                        ordering=_dumps(recommendation.ordering),
                        run_id=recommendation.run_id,
                        created_at=_as_aware(recommendation.created_at),
                        dismissed_at=(
                            None
                            if recommendation.dismissed_at is None
                            else _as_aware(recommendation.dismissed_at)
                        ),
                    )
                )
                connection.execute(
                    insert(m.advisor_audit_log).values(
                        stable_id=audit.stable_id,
                        user_id=audit_user_id,
                        run_id=audit.run_id,
                        handle=audit.handle,
                        source=audit.source,
                        outcome=audit.outcome,
                        reason=audit.reason,
                        returned_ids_json=_dumps(audit.returned_ids),
                        recorded_at=_as_aware(audit.recorded_at),
                    )
                )
        except IntegrityError as error:
            raise ValueError(
                f"duplicate recommendation or audit entry {recommendation.stable_id!r}"
            ) from error

    def get(self, stable_id: str) -> PathRecommendation | None:
        with self._engine.connect() as connection:
            return self._select_one(connection, m.path_recommendation.c.stable_id == stable_id)

    def latest_undismissed(self, user_id: str) -> PathRecommendation | None:
        t = m.path_recommendation
        with self._engine.connect() as connection:
            return self._select_one(
                connection,
                m.user.c.stable_id == user_id,
                t.c.dismissed_at.is_(None),
                order=(t.c.created_at.desc(), t.c.id.desc()),
            )

    def dismiss(self, stable_id: str, user_id: str, at: datetime) -> PathRecommendation | None:
        t = m.path_recommendation
        with self._engine.begin() as connection:
            internal = _internal_id(connection, m.user, user_id)
            if internal is None:
                return None
            # Only the first dismissal writes; a repeat keeps the original timestamp.
            connection.execute(
                update(t)
                .where(
                    t.c.stable_id == stable_id,
                    t.c.user_id == internal,
                    t.c.dismissed_at.is_(None),
                )
                .values(dismissed_at=_as_aware(at))
            )
            return self._select_one(
                connection, t.c.stable_id == stable_id, m.user.c.stable_id == user_id
            )

    def audit_entries(self, user_id: str) -> tuple[AdvisorAuditEntry, ...]:
        t = m.advisor_audit_log
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    t.c.stable_id,
                    m.user.c.stable_id,
                    t.c.run_id,
                    t.c.handle,
                    t.c.source,
                    t.c.outcome,
                    t.c.reason,
                    t.c.returned_ids_json,
                    t.c.recorded_at,
                )
                .join(m.user, m.user.c.id == t.c.user_id)
                .where(m.user.c.stable_id == user_id)
                .order_by(t.c.id)
            ).all()
        return tuple(
            AdvisorAuditEntry(
                stable_id=str(row[0]),
                user_id=str(row[1]),
                run_id=str(row[2]),
                handle=str(row[3]),
                source=str(row[4]),
                outcome=str(row[5]),
                reason=None if row[6] is None else str(row[6]),
                returned_ids=_loads(row[7]),
                recorded_at=_as_aware(row[8]),
            )
            for row in rows
        )

    def delete_audit_before(self, cutoff: datetime) -> int:
        t = m.advisor_audit_log
        with self._engine.begin() as connection:
            result = connection.execute(delete(t).where(t.c.recorded_at < _as_aware(cutoff)))
            return int(result.rowcount)

    def delete_superseded_before(self, cutoff: datetime) -> int:
        t = m.path_recommendation
        newer = m.path_recommendation.alias("newer")
        replaced = (
            select(newer.c.id)
            .where(
                newer.c.user_id == t.c.user_id,
                or_(
                    newer.c.created_at > t.c.created_at,
                    and_(newer.c.created_at == t.c.created_at, newer.c.id > t.c.id),
                ),
            )
            .exists()
        )
        with self._engine.begin() as connection:
            result = connection.execute(
                delete(t).where(t.c.created_at < _as_aware(cutoff), replaced)
            )
            return int(result.rowcount)

    def delete_for_user(self, user_id: str) -> int:
        with self._engine.begin() as connection:
            internal = _internal_id(connection, m.user, user_id)
            if internal is None:
                return 0
            removed = 0
            for table in (m.path_recommendation, m.advisor_audit_log):
                result = connection.execute(delete(table).where(table.c.user_id == internal))
                removed += int(result.rowcount)
            return removed

    @staticmethod
    def _select_one(
        connection: Any, *where: Any, order: tuple[Any, ...] = ()
    ) -> PathRecommendation | None:
        t = m.path_recommendation
        query = (
            select(
                t.c.stable_id,
                m.user.c.stable_id,
                t.c.source,
                t.c.ordering,
                t.c.run_id,
                t.c.created_at,
                t.c.dismissed_at,
            )
            .join(m.user, m.user.c.id == t.c.user_id)
            .where(*where)
        )
        if order:
            query = query.order_by(*order)
        row = connection.execute(query).first()
        if row is None:
            return None
        return PathRecommendation(
            stable_id=str(row[0]),
            user_id=str(row[1]),
            source=str(row[2]),
            ordering=_loads(row[3]),
            run_id="" if row[4] is None else str(row[4]),
            created_at=_EPOCH if row[5] is None else _as_aware(row[5]),
            dismissed_at=_as_aware_optional(row[6]),
        )
