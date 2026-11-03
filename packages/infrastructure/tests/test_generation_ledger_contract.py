"""Shared contract suite for the ``GenerationLedger`` port (ai-content task 3.2).

One parametrized suite runs against the in-memory fake and the SQL adapter: SQLite
always, PostgreSQL additionally when ``DATABASE_URL`` is set.

Validates: Requirements 1.4, 10.1, 12.1, 12.3
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.ports.generation_ledger import (
    GenerationLedger,
    JobKind,
    JobRef,
    JobStatus,
    NewJob,
)
from edu_it_domain.ports.llm_provider import AttemptUsage
from edu_it_infrastructure.fakes import In_Memory_GenerationLedger, Test_Clock
from edu_it_infrastructure.persistence import Postgres_GenerationLedger, metadata
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, func, select
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
    ledger: GenerationLedger
    clock: Test_Clock
    totals: Callable[[JobRef], tuple[int, int, int]]
    """``(tokens, cost_cents, row_count)`` as stored: job totals plus ledger rows."""
    row_cost_sum: Callable[[JobRef], int]
    ns: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    """Per-test namespace so a shared PostgreSQL database never collides across tests/runs."""

    def name(self, label: str) -> str:
        return f"{self.ns}-{label}"


def _sqlite_engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(eng)
    return eng


def _sql_backend(engine: Engine) -> _Backend:
    clock = Test_Clock(_T0)
    job, ledger = metadata.tables["generation_job"], metadata.tables["budget_ledger"]

    def totals(ref: JobRef) -> tuple[int, int, int]:
        with engine.connect() as c:
            row = c.execute(
                select(job.c.id, job.c.tokens, job.c.cost_cents).where(
                    job.c.stable_id == ref.stable_id
                )
            ).one()
            n = c.execute(
                select(func.count()).select_from(ledger).where(ledger.c.generation_job_id == row.id)
            ).scalar_one()
        return row.tokens, row.cost_cents, n

    def row_cost_sum(ref: JobRef) -> int:
        with engine.connect() as c:
            return int(
                c.execute(
                    select(func.coalesce(func.sum(ledger.c.cost_cents), 0))
                    .select_from(ledger.join(job, job.c.id == ledger.c.generation_job_id))
                    .where(job.c.stable_id == ref.stable_id)
                ).scalar_one()
            )

    return _Backend(Postgres_GenerationLedger(engine, clock), clock, totals, row_cost_sum)


@pytest.fixture(params=_BACKENDS)
def backend(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    if request.param == "fake":
        clock = Test_Clock(_T0)
        fake = In_Memory_GenerationLedger(clock)

        def totals(ref: JobRef) -> tuple[int, int, int]:
            tokens, cost = fake.job_totals(ref)
            return tokens, cost, len(fake._jobs[ref.stable_id].rows)

        def row_cost_sum(ref: JobRef) -> int:
            return sum(r.usage.cost_cents for r in fake._jobs[ref.stable_id].rows)

        yield _Backend(fake, clock, totals, row_cost_sum)
        return
    engine = _sqlite_engine() if request.param == "sqlite" else create_engine(_POSTGRES_URL)
    if request.param == "postgres":
        metadata.create_all(engine)
    try:
        yield _sql_backend(engine)
    finally:
        engine.dispose()


def _job(stable_id: str, track: str | None = "linux", **kw: object) -> NewJob:
    return NewJob(stable_id=stable_id, kind=JobKind.STAGE, track_stable_id=track, **kw)  # type: ignore[arg-type]


def _usage(cost: int, outcome: str = "ok", tin: int = 10, tout: int = 5) -> AttemptUsage:
    return AttemptUsage("m", tin, tout, cost, outcome)


def test_open_job_returns_ref_and_rejects_duplicate(backend: _Backend) -> None:
    ref = backend.ledger.open_job(_job(backend.name("j-1")))
    assert ref == JobRef(backend.name("j-1"))
    assert backend.totals(ref) == (0, 0, 0)
    with pytest.raises(ValueError):
        backend.ledger.open_job(_job(backend.name("j-1")))


def test_record_attempt_updates_rows_and_totals_together(backend: _Backend) -> None:
    ref = backend.ledger.open_job(_job(backend.name("j-1")))
    backend.ledger.record_attempt(ref, _usage(7, "invalid"))
    backend.ledger.record_attempt(ref, _usage(3, "ok", tin=1, tout=2))
    assert backend.totals(ref) == (15 + 3, 10, 2)
    assert backend.ledger.spent_for_job(ref) == 10


def test_failed_attempts_are_recorded(backend: _Backend) -> None:
    ref = backend.ledger.open_job(_job(backend.name("j-1")))
    for outcome in ("timeout", "unavailable", "binding"):
        backend.ledger.record_attempt(ref, _usage(0, outcome, tin=0, tout=0))
    assert backend.totals(ref)[2] == 3


def test_unknown_job_raises_and_writes_nothing(backend: _Backend) -> None:
    ghost = JobRef(backend.name("nope"))
    before = backend.ledger.spent_today(_T0 - timedelta(days=1))
    with pytest.raises(ValueError):
        backend.ledger.record_attempt(ghost, _usage(5))
    with pytest.raises(ValueError):
        backend.ledger.close_job(ghost, JobStatus.FAILED)
    assert backend.ledger.spent_for_job(ghost) == 0
    assert backend.ledger.spent_today(_T0 - timedelta(days=1)) == before  # nothing written


def test_close_job_keeps_totals(backend: _Backend) -> None:
    ref = backend.ledger.open_job(_job(backend.name("j-1")))
    backend.ledger.record_attempt(ref, _usage(4))
    backend.ledger.close_job(ref, JobStatus.COMPLETED)
    assert backend.totals(ref) == (15, 4, 1)


def test_spent_for_track_and_job_are_scoped(backend: _Backend) -> None:
    linux, net = backend.name("linux"), backend.name("net")
    a = backend.ledger.open_job(_job(backend.name("a"), linux))
    b = backend.ledger.open_job(_job(backend.name("b"), linux))
    c = backend.ledger.open_job(_job(backend.name("c"), net))
    backend.ledger.record_attempt(a, _usage(2))
    backend.ledger.record_attempt(b, _usage(3))
    backend.ledger.record_attempt(c, _usage(11))
    assert backend.ledger.spent_for_track(linux) == 5
    assert backend.ledger.spent_for_track(net) == 11
    assert backend.ledger.spent_for_track(backend.name("none")) == 0
    assert backend.ledger.spent_for_job(a) == 2
    assert backend.ledger.spent_for_job(c) == 11


def test_job_without_track_counts_only_for_day_and_job(backend: _Backend) -> None:
    before = backend.ledger.spent_today(_T0)
    ref = backend.ledger.open_job(_job(backend.name("j"), None))
    backend.ledger.record_attempt(ref, _usage(6))
    assert backend.ledger.spent_for_job(ref) == 6
    assert backend.ledger.spent_today(_T0) - before == 6


def test_spent_today_respects_day_start(backend: _Backend) -> None:
    # spent_today is global, so assert deltas against a shared database.
    before_all, before_late = (
        backend.ledger.spent_today(_T0),
        backend.ledger.spent_today(_T0 + timedelta(hours=1)),
    )
    ref = backend.ledger.open_job(_job(backend.name("j")))
    backend.ledger.record_attempt(ref, _usage(2))  # at T0
    backend.clock.advance(timedelta(days=1))
    backend.ledger.record_attempt(ref, _usage(5))  # at T0 + 1 day
    assert backend.ledger.spent_today(_T0 + timedelta(hours=1)) - before_late == 5
    assert backend.ledger.spent_today(_T0) - before_all == 7  # boundary is inclusive
    assert backend.ledger.spent_today(_T0 + timedelta(days=2)) == 0  # no test writes that late


@settings(max_examples=40, deadline=None)
@given(
    attempts=st.lists(
        st.tuples(
            st.integers(0, 50),
            st.integers(0, 50),
            st.integers(0, 100),
            st.sampled_from(["ok", "invalid", "timeout", "unavailable", "binding"]),
        ),
        max_size=8,
    )
)
def test_property_totals_equal_sum_of_rows(
    attempts: list[tuple[int, int, int, str]],
) -> None:
    """Rows equal attempts and the job totals equal the sum of the rows.

    **Validates: Requirements 1.4, 12.1, 12.3**
    """
    engine = _sqlite_engine()
    try:
        for b in (
            _sql_backend(engine),
            _fake_backend(),
        ):
            ref = b.ledger.open_job(_job("p"))
            for tin, tout, cost, outcome in attempts:
                b.ledger.record_attempt(ref, AttemptUsage("m", tin, tout, cost, outcome))
            tokens, cost_total, rows = b.totals(ref)
            assert rows == len(attempts)
            assert tokens == sum(t[0] + t[1] for t in attempts)
            assert cost_total == sum(t[2] for t in attempts) == b.row_cost_sum(ref)
            assert b.ledger.spent_for_job(ref) == cost_total
    finally:
        engine.dispose()


def _fake_backend() -> _Backend:
    clock = Test_Clock(_T0)
    fake = In_Memory_GenerationLedger(clock)
    return _Backend(
        fake,
        clock,
        lambda r: (*fake.job_totals(r), len(fake._jobs[r.stable_id].rows)),
        lambda r: sum(x.usage.cost_cents for x in fake._jobs[r.stable_id].rows),
    )
