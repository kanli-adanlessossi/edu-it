"""Shared contract suite for ``ItemFlagReader`` (task 12.3).

One parametrized suite runs against the in-memory fake and the SQL adapter: SQLite
always, PostgreSQL additionally when ``DATABASE_URL`` is set. Flags are set directly
through the repository, as the Milestone B serving tests do.

Validates: Requirements 14.1, 14.2, 20.3, 23.4
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from edu_it_domain.ports.item_flags import (
    FlagReason,
    FlagReasonKind,
    ItemFlagReader,
    ItemKey,
    ItemKind,
)
from edu_it_infrastructure.fakes import In_Memory_ItemFlagRepository, Test_Clock
from edu_it_infrastructure.persistence import Postgres_ItemFlagRepository, metadata
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
    repo: In_Memory_ItemFlagRepository | Postgres_ItemFlagRepository
    ns: str
    engine: Engine | None = None

    def key(self, label: str, kind: ItemKind = ItemKind.QUESTION) -> ItemKey:
        """A key unique to this test, so a shared PostgreSQL database never collides."""
        return ItemKey(kind, f"{self.ns}-{label}")


@pytest.fixture(params=_BACKENDS)
def b(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    ns = uuid.uuid4().hex[:8]
    if request.param == "fake":
        yield _Backend(In_Memory_ItemFlagRepository(), ns)
        return
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(_POSTGRES_URL)
    metadata.create_all(engine)
    try:
        yield _Backend(Postgres_ItemFlagRepository(engine, Test_Clock(_T0)), ns, engine)
    finally:
        engine.dispose()


def test_satisfies_the_reader_port(b: _Backend) -> None:
    assert isinstance(b.repo, ItemFlagReader)


def test_empty_input_and_unknown_items_give_nothing(b: _Backend) -> None:
    assert b.repo.active_flags([]) == frozenset()
    assert b.repo.active_flags([b.key("never")]) == frozenset()


def test_active_flags_returns_only_the_flagged_subset(b: _Backend) -> None:
    flagged, other = b.key("a"), b.key("b")
    assert b.repo.flag(flagged, _REASON) is True
    assert b.repo.active_flags([flagged, other]) == frozenset({flagged})


def test_kind_is_part_of_the_item_identity(b: _Backend) -> None:
    question, variant = b.key("x", ItemKind.QUESTION), b.key("x", ItemKind.VARIANT)
    b.repo.flag(variant, _REASON)
    assert b.repo.active_flags([question, variant]) == frozenset({variant})


def test_flagging_twice_is_idempotent(b: _Backend) -> None:
    key = b.key("a")
    assert b.repo.flag(key, _REASON) is True
    assert b.repo.flag(key, FlagReason(FlagReasonKind.REPORTS)) is False
    assert b.repo.active_flags([key]) == frozenset({key})


def test_cleared_flag_is_not_active_and_item_can_be_flagged_again(b: _Backend) -> None:
    key = b.key("a")
    b.repo.flag(key, _REASON)
    b.repo.clear_flag(key)
    assert b.repo.active_flags([key]) == frozenset()
    assert b.repo.flag(key, _REASON) is True
    assert b.repo.active_flags([key]) == frozenset({key})


def test_clearing_an_unflagged_item_is_a_no_op(b: _Backend) -> None:
    b.repo.clear_flag(b.key("never"))
    assert b.repo.active_flags([b.key("never")]) == frozenset()


def test_sql_partial_unique_index_rejects_a_second_active_row(b: _Backend) -> None:
    if b.engine is None:
        pytest.skip("database constraint")
    key = b.key("a")
    b.repo.flag(key, _REASON)
    table = metadata.tables["item_flag"]
    row = {
        "item_kind": key.kind.value,
        "item_stable_id": key.stable_id,
        "reason_kind": "reports",
        "flagged_at": _T0,
    }
    with pytest.raises(IntegrityError), b.engine.begin() as connection:
        connection.execute(insert(table).values(**row))
    # A cleared row alongside an active one is allowed.
    with b.engine.begin() as connection:
        connection.execute(insert(table).values(**row, cleared_at=_T0))
        count = connection.execute(
            select(table.c.id).where(table.c.item_stable_id == key.stable_id)
        ).all()
    assert len(count) == 2
