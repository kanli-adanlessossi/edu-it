"""Shared contract suite for ``PathRecommendationRepository`` (ai-content task 22.2).

One parametrized suite runs against the in-memory fake and the SQL adapter: SQLite always,
PostgreSQL additionally when ``DATABASE_URL`` is set.

Validates: Requirements 17.2, 17.6, 17.8, 21.2, 23.4
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.ports.path_recommendations import (
    AdvisorAuditEntry,
    PathRecommendation,
    PathRecommendationRepository,
)
from edu_it_infrastructure.fakes import In_Memory_PathRecommendationRepository
from edu_it_infrastructure.persistence import Postgres_PathRecommendationRepository, metadata
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
    repo: In_Memory_PathRecommendationRepository | Postgres_PathRecommendationRepository
    ns: str
    engine: Engine | None = None

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

    def run(
        self,
        label: str,
        user: str,
        *,
        minutes: int = 0,
        ordering: tuple[str, ...] = ("c1", "c2"),
        source: str = "llm",
    ) -> tuple[PathRecommendation, AdvisorAuditEntry]:
        at = _T0 + timedelta(minutes=minutes)
        recommendation = PathRecommendation(
            f"rec-{self.ns}-{label}", user, source, ordering, f"run-{label}", at
        )
        audit = AdvisorAuditEntry(
            f"aud-{self.ns}-{label}",
            user,
            f"run-{label}",
            f"h_{label}",
            source,
            "accepted",
            at,
            None,
            ordering,
        )
        return recommendation, audit


@pytest.fixture(params=_BACKENDS)
def b(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    ns = uuid.uuid4().hex[:8]
    if request.param == "fake":
        yield _Backend(In_Memory_PathRecommendationRepository(), ns)
        return
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(_POSTGRES_URL)
    metadata.create_all(engine)
    try:
        yield _Backend(Postgres_PathRecommendationRepository(engine), ns, engine)
    finally:
        engine.dispose()


def test_satisfies_the_port(b: _Backend) -> None:
    assert isinstance(b.repo, PathRecommendationRepository)


def test_record_run_round_trips_recommendation_and_audit(b: _Backend) -> None:
    user = b.user("a")
    recommendation, audit = b.run("1", user)
    b.repo.record_run(recommendation, audit)
    assert b.repo.get(recommendation.stable_id) == recommendation
    assert b.repo.get(f"rec-{b.ns}-missing") is None
    assert b.repo.audit_entries(user) == (audit,)

    empty, empty_audit = b.run("2", user, ordering=())
    reasoned = AdvisorAuditEntry(
        empty_audit.stable_id,
        user,
        empty_audit.run_id,
        empty_audit.handle,
        "rule-based",
        "discarded",
        _T0,
        "ids not eligible: ghost",
        ("ghost",),
    )
    b.repo.record_run(empty, reasoned)
    assert b.repo.get(empty.stable_id) == empty
    assert b.repo.audit_entries(user) == (audit, reasoned)


def test_a_repeated_stable_id_is_rejected_and_writes_nothing(b: _Backend) -> None:
    user = b.user("a")
    recommendation, audit = b.run("1", user)
    b.repo.record_run(recommendation, audit)
    again, other_audit = b.run("2", user)
    clash = PathRecommendation(recommendation.stable_id, user, "llm", ("c9",), "run-2", _T0)
    with pytest.raises(ValueError, match="duplicate"):
        b.repo.record_run(clash, other_audit)
    # The audit row of the failed run was not kept either.
    assert b.repo.audit_entries(user) == (audit,)
    assert b.repo.get(again.stable_id) is None


def test_latest_undismissed_is_the_newest_and_skips_dismissed(b: _Backend) -> None:
    user, other = b.user("a"), b.user("b")
    first, first_audit = b.run("1", user, minutes=0)
    second, second_audit = b.run("2", user, minutes=5)
    theirs, theirs_audit = b.run("3", other, minutes=9)
    assert b.repo.latest_undismissed(user) is None
    for rec, audit in ((first, first_audit), (second, second_audit), (theirs, theirs_audit)):
        b.repo.record_run(rec, audit)
    assert b.repo.latest_undismissed(user) == second
    assert b.repo.latest_undismissed(other) == theirs

    dismissed = b.repo.dismiss(second.stable_id, user, _T0 + timedelta(hours=1))
    assert dismissed is not None and dismissed.dismissed_at == _T0 + timedelta(hours=1)
    assert b.repo.latest_undismissed(user) == first
    b.repo.dismiss(first.stable_id, user, _T0 + timedelta(hours=2))
    assert b.repo.latest_undismissed(user) is None
    assert b.repo.latest_undismissed(other) == theirs


def test_dismiss_is_idempotent_and_only_for_the_owner(b: _Backend) -> None:
    user, other = b.user("a"), b.user("b")
    recommendation, audit = b.run("1", user)
    b.repo.record_run(recommendation, audit)
    first_at = _T0 + timedelta(hours=1)

    assert b.repo.dismiss(recommendation.stable_id, other, first_at) is None
    assert b.repo.dismiss(f"rec-{b.ns}-missing", user, first_at) is None
    assert b.repo.get(recommendation.stable_id) == recommendation  # untouched

    once = b.repo.dismiss(recommendation.stable_id, user, first_at)
    twice = b.repo.dismiss(recommendation.stable_id, user, first_at + timedelta(hours=5))
    assert once is not None and twice == once
    assert twice.dismissed_at == first_at  # the first dismissal is kept


def test_delete_for_user_removes_only_that_learners_rows(b: _Backend) -> None:
    u1, u2 = b.user("1"), b.user("2")
    r1, a1 = b.run("1", u1)
    r1b, a1b = b.run("2", u1, minutes=1)
    r2, a2 = b.run("3", u2)
    for rec, audit in ((r1, a1), (r1b, a1b), (r2, a2)):
        b.repo.record_run(rec, audit)
    b.repo.dismiss(r1.stable_id, u1, _T0)

    assert b.repo.delete_for_user(u1) == 4  # two recommendations and two audit rows
    assert b.repo.get(r1.stable_id) is None
    assert b.repo.audit_entries(u1) == ()
    assert b.repo.get(r2.stable_id) == r2
    assert b.repo.audit_entries(u2) == (a2,)
    assert b.repo.delete_for_user(u1) == 0
    assert b.repo.delete_for_user(f"usr-{b.ns}-unknown") == 0


_OLD = datetime(2001, 1, 1, tzinfo=UTC)  # far before the other tests' rows (shared PostgreSQL)


def _at(
    b: _Backend, label: str, user: str, when: datetime
) -> tuple[PathRecommendation, AdvisorAuditEntry]:
    rec, audit = b.run(label, user)
    return (
        PathRecommendation(rec.stable_id, user, rec.source, rec.ordering, rec.run_id, when),
        AdvisorAuditEntry(
            audit.stable_id, user, audit.run_id, audit.handle, audit.source, audit.outcome, when
        ),
    )


def test_delete_audit_before_removes_only_strictly_older_entries(b: _Backend) -> None:
    user = b.user("a")
    edge = _OLD + timedelta(days=10)
    for label, when in (("old", _OLD), ("edge", edge), ("new", _T0)):
        b.repo.record_run(*_at(b, label, user, when))
    assert b.repo.delete_audit_before(edge) == 1
    assert [a.stable_id for a in b.repo.audit_entries(user)] == [
        f"aud-{b.ns}-edge",
        f"aud-{b.ns}-new",
    ]
    assert b.repo.delete_audit_before(edge) == 0  # idempotent
    assert b.repo.get(f"rec-{b.ns}-old") is not None  # recommendations are not audit rows


def test_delete_superseded_before_keeps_the_newest_of_each_learner(b: _Backend) -> None:
    u1, u2 = b.user("1"), b.user("2")
    edge = _OLD + timedelta(days=10)
    b.repo.record_run(*_at(b, "a-old", u1, _OLD))  # superseded by a-edge: purged
    b.repo.record_run(*_at(b, "a-edge", u1, edge))  # not older than the cutoff: kept
    b.repo.record_run(*_at(b, "b-old", u2, _OLD))  # old but the newest of u2: kept
    assert b.repo.delete_superseded_before(edge) == 1
    assert b.repo.get(f"rec-{b.ns}-a-old") is None
    assert b.repo.get(f"rec-{b.ns}-a-edge") is not None
    assert b.repo.get(f"rec-{b.ns}-b-old") is not None
    assert b.repo.delete_superseded_before(edge) == 0  # idempotent
    # Once a newer run exists the old one is superseded.
    b.repo.record_run(*_at(b, "b-new", u2, _T0))
    assert b.repo.delete_superseded_before(edge) == 1
    assert b.repo.get(f"rec-{b.ns}-b-old") is None
