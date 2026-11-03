"""Shared contract suite for ``ProblemReportRepository`` (ai-content task 20.1).

One parametrized suite runs against the in-memory fake and the SQL adapter: SQLite always,
PostgreSQL additionally when ``DATABASE_URL`` is set.

Validates: Requirements 18.2, 21.2, 23.4
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.problem_reports import ProblemReport, ProblemReportRepository
from edu_it_infrastructure.fakes import In_Memory_ProblemReportRepository
from edu_it_infrastructure.persistence import Postgres_ProblemReportRepository, metadata
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES_URL = _DATABASE_URL if _DATABASE_URL.startswith("postgresql") else ""

_BACKENDS = [
    "fake",
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(not _POSTGRES_URL, reason="DATABASE_URL (PostgreSQL) not set"),
    ),
]


@dataclass
class _Backend:
    repo: In_Memory_ProblemReportRepository | Postgres_ProblemReportRepository
    ns: str
    engine: Engine | None = None

    def key(self, label: str) -> ItemKey:
        return ItemKey(ItemKind.QUESTION, f"{self.ns}-{label}")

    def user(self, label: str) -> str:
        stable_id = f"usr-{self.ns}-{label}"
        if self.engine is not None:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(metadata.tables["user"]).values(
                        stable_id=stable_id,
                        email=f"{stable_id}@example.test",
                        locale="en",
                        status="verified",
                    )
                )
        return stable_id

    def report(
        self,
        label: str,
        user: str,
        key: ItemKey,
        version: str = "v1",
        comment: str | None = "unclear wording",
    ) -> ProblemReport:
        return ProblemReport(f"pr-{self.ns}-{label}", user, key, version, "unclear", _T0, comment)


@pytest.fixture(params=_BACKENDS)
def b(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    ns = uuid.uuid4().hex[:8]
    if request.param == "fake":
        yield _Backend(In_Memory_ProblemReportRepository(), ns)
        return
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(_POSTGRES_URL)
    metadata.create_all(engine)
    try:
        yield _Backend(Postgres_ProblemReportRepository(engine), ns, engine)
    finally:
        engine.dispose()


def test_satisfies_the_port(b: _Backend) -> None:
    assert isinstance(b.repo, ProblemReportRepository)


def test_add_then_get_round_trips_and_unknown_is_none(b: _Backend) -> None:
    user = b.user("a")
    stored = b.report("1", user, b.key("q"))
    b.repo.add(stored)
    assert b.repo.get(stored.stable_id) == stored
    no_comment = b.report("2", user, b.key("q"), comment=None)
    b.repo.add(no_comment)
    assert b.repo.get(no_comment.stable_id) == no_comment
    assert b.repo.get(f"pr-{b.ns}-missing") is None


def test_a_repeated_stable_id_is_rejected(b: _Backend) -> None:
    user = b.user("a")
    b.repo.add(b.report("1", user, b.key("q")))
    with pytest.raises(ValueError, match="duplicate"):
        b.repo.add(b.report("1", user, b.key("q")))


def test_reporters_are_distinct_learners_of_exactly_that_version(b: _Backend) -> None:
    u1, u2, u3 = b.user("1"), b.user("2"), b.user("3")
    q, other = b.key("q"), b.key("other")
    b.repo.add(b.report("a", u1, q, "v1"))
    b.repo.add(b.report("b", u1, q, "v1"))  # the same learner twice counts once
    b.repo.add(b.report("c", u2, q, "v1"))
    b.repo.add(b.report("d", u3, q, "v2"))  # another version
    b.repo.add(b.report("e", u3, other, "v1"))  # another item
    assert b.repo.reporters_for(q, "v1") == frozenset({u1, u2})
    assert b.repo.reporters_for(q, "v2") == frozenset({u3})
    assert b.repo.reporters_for(q, "v3") == frozenset()


def test_delete_for_user_removes_only_that_learners_reports(b: _Backend) -> None:
    u1, u2 = b.user("1"), b.user("2")
    q = b.key("q")
    b.repo.add(b.report("a", u1, q))
    b.repo.add(b.report("b", u1, q))
    b.repo.add(b.report("c", u2, q))
    assert b.repo.delete_for_user(u1) == 2
    assert b.repo.get(f"pr-{b.ns}-a") is None
    assert b.repo.reporters_for(q, "v1") == frozenset({u2})
    assert b.repo.delete_for_user(u1) == 0
    assert b.repo.delete_for_user(f"usr-{b.ns}-unknown") == 0


def test_delete_created_before_removes_only_strictly_older_reports(b: _Backend) -> None:
    # Dates far before the other tests' rows, so a shared PostgreSQL database is not disturbed.
    old, edge = datetime(2001, 1, 1, tzinfo=UTC), datetime(2001, 6, 1, tzinfo=UTC)
    user = b.user("a")
    q = b.key("q")
    for label, at in (("old", old), ("edge", edge), ("new", _T0)):
        r = b.report(label, user, q)
        b.repo.add(ProblemReport(r.stable_id, r.user_id, r.key, "v1", "unclear", at, None))
    assert b.repo.delete_created_before(edge) == 1
    assert b.repo.get(f"pr-{b.ns}-old") is None
    assert b.repo.get(f"pr-{b.ns}-edge") is not None
    assert b.repo.get(f"pr-{b.ns}-new") is not None
    assert b.repo.delete_created_before(edge) == 0  # idempotent
