"""Shared contract suite for ``ItemQualityRepository`` and ``ObservationRecorder`` (task 19.2).

One parametrized suite runs against the in-memory fake and the SQL adapter: SQLite always,
PostgreSQL additionally when ``DATABASE_URL`` is set. It also re-runs the flag behaviour of
the ``ItemFlagReader`` it extends.

Validates: Requirements 19.1, 21.4, 23.4
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from edu_it_domain.ai.quality import ItemObservation, ItemStat, fold
from edu_it_domain.ports.item_flags import (
    FlagReason,
    FlagReasonKind,
    ItemFlagReader,
    ItemKey,
    ItemKind,
)
from edu_it_domain.ports.item_quality import (
    ItemQualityRepository,
    NewObservation,
    ObservationRecorder,
)
from edu_it_infrastructure.fakes import In_Memory_ItemQualityRepository, Test_Clock
from edu_it_infrastructure.persistence import Postgres_ItemQualityRepository, metadata
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
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

_REASON = FlagReason(FlagReasonKind.STATISTICS, "too many wrong answers")


@dataclass
class _Backend:
    repo: In_Memory_ItemQualityRepository | Postgres_ItemQualityRepository
    ns: str
    engine: Engine | None = None

    def key(self, label: str, kind: ItemKind = ItemKind.QUESTION) -> ItemKey:
        """A key unique to this test, so a shared PostgreSQL database never collides."""
        return ItemKey(kind, f"{self.ns}-{label}")

    def user(self) -> str:
        """A learner known to the backend (the SQL adapter needs a ``user`` row)."""
        stable_id = f"usr-{self.ns}"
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

    def obs(
        self, user: str, key: ItemKey, correct: bool = True, elapsed_ms: int | None = None
    ) -> NewObservation:
        return NewObservation(user, key, correct, _T0, elapsed_ms)


@pytest.fixture(params=_BACKENDS)
def b(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    ns = uuid.uuid4().hex[:8]
    if request.param == "fake":
        yield _Backend(In_Memory_ItemQualityRepository(), ns)
        return
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(_POSTGRES_URL)
    metadata.create_all(engine)
    try:
        yield _Backend(Postgres_ItemQualityRepository(engine, Test_Clock(_T0)), ns, engine)
    finally:
        engine.dispose()


def test_satisfies_the_ports(b: _Backend) -> None:
    assert isinstance(b.repo, ItemQualityRepository)
    assert isinstance(b.repo, ItemFlagReader)
    assert isinstance(b.repo, ObservationRecorder)


def test_observations_come_back_in_order_after_a_cursor(b: _Backend) -> None:
    user = b.user()
    first, second, third = b.key("a"), b.key("b", ItemKind.VARIANT), b.key("c")
    b.repo.record(b.obs(user, first, True, 1500))
    b.repo.record(b.obs(user, second, False))
    b.repo.record(b.obs(user, third, True, 0))

    page = b.repo.observations_after(0, 2)
    assert [(o.key, o.correct, o.elapsed_ms) for o in page] == [
        (first, True, 1500),
        (second, False, None),
    ]
    assert page[0].id < page[1].id
    rest = b.repo.observations_after(page[-1].id, 10)
    assert [o.key for o in rest] == [third]
    assert b.repo.observations_after(rest[-1].id, 10) == []
    assert page[0].as_observation() == ItemObservation(
        (first.kind.value, first.stable_id), True, 1500
    )


def test_limit_must_be_positive(b: _Backend) -> None:
    with pytest.raises(ValueError, match="limit"):
        b.repo.observations_after(0, 0)


def test_variant_ref_is_stored_with_the_observation(b: _Backend) -> None:
    if b.engine is None:
        pytest.skip("column check")
    user = b.user()
    key = b.key("v", ItemKind.VARIANT)
    b.repo.record(NewObservation(user, key, True, _T0, variant_ref=f"{key.stable_id}@v1"))
    table = metadata.tables["item_observation"]
    with b.engine.connect() as connection:
        stored: str = connection.execute(
            select(table.c.variant_ref).where(table.c.item_stable_id == key.stable_id)
        ).scalar_one()
    assert stored == f"{key.stable_id}@v1"


def test_cursor_starts_at_zero_and_apply_stores_stats_and_cursor_together(b: _Backend) -> None:
    key = b.key("a")
    assert b.repo.cursor() == 0
    assert b.repo.get(key) is None

    stat = fold(
        fold(ItemStat(), ItemObservation((key.kind.value, key.stable_id), True, 4000)),
        ItemObservation((key.kind.value, key.stable_id), False),
    )
    b.repo.apply({key: stat}, 7)
    assert b.repo.cursor() == 7
    assert b.repo.get(key) == stat
    assert stat == ItemStat(samples=2, correct_count=1, time_samples=1, time_total_ms=4000)

    # A later fold replaces the stored values; the same key never gains a second row.
    later = fold(stat, ItemObservation((key.kind.value, key.stable_id), True, 2000))
    b.repo.apply({key: later}, 9)
    assert b.repo.cursor() == 9
    assert b.repo.get(key) == later


def test_apply_with_no_stats_still_moves_the_cursor(b: _Backend) -> None:
    b.repo.apply({}, 3)
    assert b.repo.cursor() == 3


def test_stored_row_keeps_the_derived_rate_and_average(b: _Backend) -> None:
    if b.engine is None:
        pytest.skip("column check")
    key = b.key("a")
    b.repo.apply({key: ItemStat(4, 3, 2, 9000)}, 1)
    table = metadata.tables["item_statistic"]
    with b.engine.connect() as connection:
        row = connection.execute(
            select(table.c.correct_rate, table.c.avg_time_ms, table.c.updated_at).where(
                table.c.item_stable_id == key.stable_id
            )
        ).one()
    assert row[0] == pytest.approx(0.75)
    assert row[1] == pytest.approx(4500.0)
    assert row[2] is not None


def test_statistics_carry_no_learner_link(b: _Backend) -> None:
    table = metadata.tables["item_statistic"]
    assert "user_id" not in table.c


def test_apply_is_atomic_when_a_statistic_cannot_be_stored(b: _Backend) -> None:
    if b.engine is None:
        pytest.skip("transaction check")
    key = b.key("a")
    b.repo.apply({key: ItemStat(1, 1, 0, 0)}, 2)
    broken = ItemStat(samples=1, correct_count=1, time_samples=0, time_total_ms=0)
    object.__setattr__(broken, "samples", "not-a-number")  # forces a database error mid-apply
    with pytest.raises(Exception):  # noqa: B017  (driver specific error type)
        b.repo.apply({b.key("b"): ItemStat(1, 1, 0, 0), key: broken}, 99)
    assert b.repo.cursor() == 2
    assert b.repo.get(b.key("b")) is None
    assert b.repo.get(key) == ItemStat(1, 1, 0, 0)


def test_flag_is_idempotent_and_clear_allows_a_new_flag(b: _Backend) -> None:
    key = b.key("a")
    assert b.repo.flag(key, _REASON) is True
    assert b.repo.flag(key, FlagReason(FlagReasonKind.REPORTS)) is False
    assert b.repo.active_flags([key, b.key("other")]) == frozenset({key})
    b.repo.clear_flag(key)
    assert b.repo.active_flags([key]) == frozenset()
    assert b.repo.flag(key, _REASON) is True


def test_clearing_an_unflagged_item_is_a_no_op(b: _Backend) -> None:
    b.repo.clear_flag(b.key("never"))
    assert b.repo.active_flags([b.key("never")]) == frozenset()


def test_sql_unique_index_rejects_a_second_statistic_row_for_one_item(b: _Backend) -> None:
    if b.engine is None:
        pytest.skip("database constraint")
    key = b.key("a")
    b.repo.apply({key: ItemStat(1, 1, 0, 0)}, 1)
    table = metadata.tables["item_statistic"]
    with pytest.raises(IntegrityError), b.engine.begin() as connection:
        connection.execute(
            insert(table).values(item_kind=key.kind.value, item_stable_id=key.stable_id)
        )


def test_delete_observations_before_removes_only_older_ones_and_keeps_ids_monotonic(
    b: _Backend,
) -> None:
    old, edge = datetime(2001, 1, 1, tzinfo=UTC), datetime(2001, 6, 1, tzinfo=UTC)
    user, key = b.user(), b.key("q")
    for at in (old, edge, _T0):
        b.repo.record(NewObservation(user, key, True, at))
    before = {o.id for o in b.repo.observations_after(0, 100_000)}
    removed = b.repo.delete_observations_before(edge)
    assert removed == 1
    after = b.repo.observations_after(0, 100_000)
    assert len(after) == len(before) - 1
    assert b.repo.delete_observations_before(edge) == 0  # idempotent
    b.repo.record(NewObservation(user, key, False, _T0))
    newest = max(o.id for o in b.repo.observations_after(0, 100_000))
    assert newest > max(before)  # a deleted id is never reused
