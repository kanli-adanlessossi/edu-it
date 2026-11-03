"""The SQL source of the path advisor's targets (ai-content task 22.3).

Which learners have activity (a ``mastery`` row) and which concepts exist. Read only, over
``mastery``, ``user`` and ``concept``. Database-agnostic SQLAlchemy Core.
"""

from __future__ import annotations

import importlib
from typing import Any

from sqlalchemy import select

from edu_it_infrastructure.persistence.learning_repositories import _Base

m: Any = importlib.import_module("edu_it_infrastructure.persistence.metadata")

__all__ = ["Postgres_AdvisorTargets"]


class Postgres_AdvisorTargets(_Base):
    """Learners with activity and candidate concepts, by ``stable_id`` (sorted)."""

    def active_learners(self) -> tuple[str, ...]:
        """Learners who have at least one ``mastery`` row."""
        stmt = (
            select(m.user.c.stable_id)
            .where(m.user.c.id.in_(select(m.mastery.c.user_id)))
            .order_by(m.user.c.stable_id)
        )
        with self._engine.connect() as connection:
            return tuple(str(row[0]) for row in connection.execute(stmt))

    def candidate_concepts(self) -> tuple[str, ...]:
        """Every concept of the catalog; eligibility is decided later by ``EligibleItems``."""
        stmt = select(m.concept.c.stable_id).order_by(m.concept.c.stable_id)
        with self._engine.connect() as connection:
            return tuple(str(row[0]) for row in connection.execute(stmt))
