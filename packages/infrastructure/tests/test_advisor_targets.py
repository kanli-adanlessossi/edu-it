"""Direct tests for ``Postgres_AdvisorTargets`` (ai-content task 22.3).

SQLite always; PostgreSQL additionally when ``DATABASE_URL`` is set.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from edu_it_infrastructure.persistence import metadata
from edu_it_infrastructure.persistence.advisor_targets import Postgres_AdvisorTargets
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES_URL = _DATABASE_URL if _DATABASE_URL.startswith("postgresql") else ""

_BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(not _POSTGRES_URL, reason="DATABASE_URL (PostgreSQL) not set"),
    ),
]


@pytest.fixture(params=_BACKENDS)
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    if request.param == "sqlite":
        eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        eng = create_engine(_POSTGRES_URL)
    metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _user(engine: Engine, stable_id: str) -> int:
    with engine.begin() as connection:
        result = connection.execute(
            insert(metadata.tables["user"])
            .values(
                stable_id=stable_id,
                email=f"{stable_id}@example.test",
                locale="en",
                status="verified",
            )
            .returning(metadata.tables["user"].c.id)
        )
        return int(result.scalar_one())


def _concept(engine: Engine, stable_id: str) -> int:
    with engine.begin() as connection:
        prov = connection.execute(
            insert(metadata.tables["content_provenance"])
            .values(stable_id=f"prov-{stable_id}", content_version="v1", origin="human")
            .returning(metadata.tables["content_provenance"].c.id)
        )
        result = connection.execute(
            insert(metadata.tables["concept"])
            .values(
                stable_id=stable_id,
                provenance_id=int(prov.scalar_one()),
                title_fr=stable_id,
                title_en=stable_id,
            )
            .returning(metadata.tables["concept"].c.id)
        )
        return int(result.scalar_one())


def test_empty_database_has_no_targets(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("needs an empty database; a shared PostgreSQL database accumulates rows")
    targets = Postgres_AdvisorTargets(engine)
    assert targets.active_learners() == ()
    assert targets.candidate_concepts() == ()


def test_active_learners_are_those_with_mastery_sorted_and_distinct(engine: Engine) -> None:
    ns = uuid.uuid4().hex[:8]
    active_b, active_a, idle = (f"usr-{ns}-{x}" for x in ("b", "a", "z"))
    ub, ua = _user(engine, active_b), _user(engine, active_a)
    _user(engine, idle)
    c1, c2 = _concept(engine, f"cn-{ns}-1"), _concept(engine, f"cn-{ns}-2")
    with engine.begin() as connection:
        connection.execute(
            insert(metadata.tables["mastery"]),
            [
                {"user_id": ub, "concept_id": c1, "score": 10, "state": "learning"},
                {"user_id": ub, "concept_id": c2, "score": 20, "state": "learning"},
                {"user_id": ua, "concept_id": c1, "score": 30, "state": "learning"},
            ],
        )
    learners = Postgres_AdvisorTargets(engine).active_learners()
    assert tuple(s for s in learners if f"-{ns}-" in s) == (active_a, active_b)


def test_candidate_concepts_are_every_concept_sorted(engine: Engine) -> None:
    ns = uuid.uuid4().hex[:8]
    for suffix in ("c", "a", "b"):
        _concept(engine, f"cn-{ns}-{suffix}")
    concepts = Postgres_AdvisorTargets(engine).candidate_concepts()
    assert tuple(s for s in concepts if f"-{ns}-" in s) == tuple(
        f"cn-{ns}-{s}" for s in ("a", "b", "c")
    )
