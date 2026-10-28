"""Property-based test for the apply-then-rollback round trip (task 6.3).

Feature: foundations, Property 33: Apply-then-rollback round-trips an empty database to empty

Property 33 (design.md): *For any* empty database, upgrading to head and then
downgrading to base removes every Data_Model table in reverse dependency order,
returning the database to having no Data_Model table, with both operations
exiting success.

This is the universal round-trip property. The set-equality of tables against
the Data_Model entities and the mid-migration failure behavior are covered by
the example/unit tests in task 6.5; idempotent re-apply is task 6.2.

Validates: Requirements 4.3

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).

The lifecycle check in task 6.1 drove the Migration_Runner against a throwaway
SQLite database because no live PostgreSQL is provisioned; this test follows the
same approach. Each example runs against its own fresh, empty on-disk SQLite
database so the "empty database" precondition holds for every iteration and the
runs are fully independent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from edu_it_infrastructure.persistence import DATA_MODEL_TABLE_NAMES
from edu_it_infrastructure.persistence.runner import Migration_Runner
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, inspect

# Alembic's own bookkeeping table is not a Data_Model table; exclude it when
# measuring the application tables present in the database.
_ALEMBIC_BOOKKEEPING = "alembic_version"

# The concrete head revision that builds the full Data_Model schema. We target
# this revision explicitly rather than the ambient Alembic ``head`` so the
# round trip is pinned to the Data_Model schema and the assertions can name the
# expected version. The migration tests no longer mutate the shared
# ``versions/`` directory (the failing-revision test uses an isolated
# version_locations directory), so the ambient head equals this revision too;
# naming it here keeps the property self-describing.
_DATA_MODEL_HEAD = "0001_create_data_model"


def _application_tables(database_url: str) -> frozenset[str]:
    """Return the Data_Model tables physically present in the database.

    Reflects the live schema via the dialect inspector and drops Alembic's
    ``alembic_version`` bookkeeping table, leaving only the application tables
    that the migrations create and drop.
    """
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            names = set(inspect(connection).get_table_names())
    finally:
        engine.dispose()
    names.discard(_ALEMBIC_BOOKKEEPING)
    return frozenset(names)


# The property quantifies over empty databases; an empty database is fully
# characterized by "no application tables present", so every drawn example is
# an equivalent empty starting point. We draw a small opaque seed only to let
# Hypothesis run the required number of independent iterations, each against its
# own freshly created empty database.
@given(seed=st.integers())
@settings(max_examples=100, deadline=None)
def test_property_33_apply_then_rollback_round_trips_empty_to_empty(seed: int) -> None:
    """Round-trip an empty database to empty via apply-then-rollback.

    Feature: foundations, Property 33: Apply-then-rollback round-trips an empty
    database to empty.

    Starting from an empty database, ``upgrade`` to head then ``downgrade`` to
    base both succeed and leave the database with no Data_Model table — the set
    of application tables before equals the set after (both empty) (Req 4.3).
    """
    del seed  # only used to drive independent Hypothesis iterations

    with tempfile.TemporaryDirectory() as tmp:
        database_url = f"sqlite:///{Path(tmp) / 'roundtrip.db'}"

        # Precondition: the database starts empty (no application tables).
        before = _application_tables(database_url)
        assert before == frozenset(), (
            f"expected an empty database to start, found tables {sorted(before)}"
        )

        runner = Migration_Runner(database_url)

        # Apply the Data_Model migrations to their head: creates the complete
        # schema and succeeds (no exception == success). Confirm the tables are
        # actually present so the subsequent rollback is a meaningful round trip
        # and not a no-op.
        runner.upgrade(_DATA_MODEL_HEAD)
        assert runner.current() == _DATA_MODEL_HEAD
        at_head = _application_tables(database_url)
        assert at_head == DATA_MODEL_TABLE_NAMES, (
            "upgrade to head did not create exactly the Data_Model tables; "
            f"missing={sorted(DATA_MODEL_TABLE_NAMES - at_head)} "
            f"extra={sorted(at_head - DATA_MODEL_TABLE_NAMES)}"
        )

        # Roll back to base: removes every Data_Model table and succeeds.
        runner.downgrade("base")
        assert runner.current() is None

        # The round trip returns the database to empty: tables after == before.
        after = _application_tables(database_url)
        assert after == before == frozenset(), (
            f"rollback left Data_Model tables behind: {sorted(after)}"
        )
