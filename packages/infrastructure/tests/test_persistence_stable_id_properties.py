"""Property-based test for stable_id uniqueness and the hidden internal key (task 6.4).

Feature: foundations, Property 34: Every API-addressable resource has a unique stable_id and hides its internal key

Property 34 (design.md): *For any* API-addressable resource in the migrated
schema, the table carries an immutable ``stable_id`` column under a unique
constraint and the resource's serialized API representation omits the internal
numeric primary key.

This exercises the universal property across every API-addressable table
(``STABLE_ID_TABLE_NAMES``): the ``stable_id`` uniqueness is enforced by the
database (a second row re-using an existing ``stable_id`` is rejected), and the
internal numeric primary key is a separate column named ``id`` that is distinct
from ``stable_id`` — the surrogate key the design keeps unexposed. Set-equality
of the entity/table set and the mid-migration failure behaviour are covered by
the unit tests in task 6.5.

Validates: Requirements 4.4

The property is checked against a throwaway SQLite database applied by the real
:class:`~edu_it_infrastructure.persistence.runner.Migration_Runner`, following
the same approach task 6.1 used for lifecycle verification because no live
PostgreSQL is provisioned. SQLite enforces column ``UNIQUE`` constraints, which
is exactly what this property asserts; foreign-key checks are left off (SQLite's
default) so each table can be probed in isolation.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from edu_it_infrastructure.persistence.metadata import (
    STABLE_ID_TABLE_NAMES,
    metadata,
)
from edu_it_infrastructure.persistence.runner import Migration_Runner
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Table,
    create_engine,
    insert,
)
from sqlalchemy.exc import IntegrityError

# The sorted list of API-addressable tables, used as a deterministic sampling
# space for Hypothesis so a failing example names a concrete table.
_STABLE_ID_TABLES: list[str] = sorted(STABLE_ID_TABLE_NAMES)


@pytest.fixture(scope="module")
def migrated_sqlite_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Apply the full schema to a throwaway SQLite file and yield its URL.

    A file-backed SQLite database (not ``:memory:``) is used so the schema
    created by one engine/connection is visible to the per-example engines the
    test opens. Built once per module; the temp directory is cleaned up by
    pytest.
    """
    db_path: Path = tmp_path_factory.mktemp("stable_id") / "schema.sqlite"
    url = f"sqlite:///{db_path}"

    runner = Migration_Runner(url)
    runner.upgrade("head")
    # Sanity: the schema really was applied (Req 4.2) before probing it.
    assert runner.current() == runner.head()

    yield url


def _required_value(column: Any, salt: int) -> Any:
    """Produce a type-appropriate non-null value for a required ``column``.

    Covers the SQLAlchemy column types used by the schema. ``salt`` keeps values
    that must differ between the two inserted rows (e.g. other unique columns
    such as ``quiz_attempt.idempotency_key``) distinct, so the only collision
    under test is on ``stable_id``.
    """
    type_ = column.type
    if isinstance(type_, Boolean):
        return False
    if isinstance(type_, Integer):
        return salt
    if isinstance(type_, Float):
        return float(salt)
    if isinstance(type_, DateTime):
        return None  # Required columns here are never DateTime; kept for safety.
    if isinstance(type_, String):
        return f"{column.name}-{salt}"
    # Any other type: a string is broadly acceptable for the probe.
    return f"{column.name}-{salt}"


def _row_for(table: Table, *, stable_id: str, salt: int) -> dict[str, Any]:
    """Build a row for ``table`` filling every required column but ``id``.

    ``id`` is the autoincrement surrogate key and is left to the database so the
    two probe rows get distinct internal keys. ``stable_id`` is set to the
    supplied value; every other non-nullable column (including other unique
    columns) is filled with a ``salt``-distinct value so only ``stable_id`` can
    collide.
    """
    row: dict[str, Any] = {"stable_id": stable_id}
    for column in table.columns:
        if column.name in ("id", "stable_id"):
            continue
        if column.nullable:
            continue
        row[column.name] = _required_value(column, salt)
    return row


# deadline=None: each example opens a fresh engine and performs several SQLite
# round-trips, so per-example timing varies (first-call warmup can exceed the
# default deadline); the property is about DB-enforced uniqueness, not latency.
@settings(max_examples=120, deadline=None)
@given(
    table_name=st.sampled_from(_STABLE_ID_TABLES),
    stable_id=st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=64,
    ),
)
def test_property_34_unique_stable_id_and_hidden_internal_key(
    migrated_sqlite_url: str,
    table_name: str,
    stable_id: str,
) -> None:
    """Feature: foundations, Property 34: Every API-addressable resource has a unique stable_id and hides its internal key.

    For the chosen API-addressable table: (1) the internal numeric primary key
    is a column named ``id`` distinct from ``stable_id`` (the surrogate key kept
    unexposed), and (2) the database rejects a second row that re-uses an
    already-inserted ``stable_id`` — the unique constraint is enforced — while a
    distinct ``stable_id`` is accepted.
    """
    table = metadata.tables[table_name]

    # (2 — structure) The internal key is a numeric PK named ``id`` that is
    # never the stable_id; stable_id is a separate, non-primary-key column.
    pk_columns = [column.name for column in table.primary_key.columns]
    assert pk_columns == ["id"]
    assert isinstance(table.c["id"].type, Integer)
    assert "stable_id" in table.c
    assert "stable_id" not in pk_columns

    engine = create_engine(migrated_sqlite_url)
    try:
        # Committed transaction: two rows with *distinct* stable_ids are both
        # accepted. ``id`` is assigned by the database, proving the internal key
        # is independent of the supplied ``stable_id``.
        with engine.begin() as connection:
            connection.execute(insert(table), _row_for(table, stable_id=stable_id, salt=1))
            connection.execute(
                insert(table),
                _row_for(table, stable_id=f"{stable_id}-other", salt=2),
            )

        # Separate transaction: re-using an existing stable_id is rejected by
        # the unique constraint. salt=3 keeps every other (possibly unique)
        # column distinct so the only collision is on stable_id. The failed
        # transaction rolls back, so the duplicate is never persisted.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                insert(table),
                _row_for(table, stable_id=stable_id, salt=3),
            )

        # Clean up the two committed probe rows so each example starts from a
        # fresh, empty table and the shared module-scoped database never
        # accumulates state across Hypothesis examples.
        with engine.begin() as connection:
            connection.execute(table.delete())
    finally:
        engine.dispose()
