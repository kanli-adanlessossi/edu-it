"""Property-based test for idempotent migrate-to-head (task 6.2).

Feature: foundations, Property 32: Migrating to head is idempotent

Property 32 (design.md): *For any* database already at the latest migration
version, applying migrations to head again makes no schema change and exits
success, so the schema after a second apply equals the schema after the first.

Validates: Requirements 4.6

The design specifies these migration properties run against a disposable
PostgreSQL via the :class:`Migration_Runner`. No live PostgreSQL is provisioned
in this environment, so — following the lifecycle verification approach from
task 6.1 — the test runs against a throwaway database: it uses the database at
``EDU_IT_TEST_DATABASE_URL`` (or ``DATABASE_URL``) when one is reachable and
otherwise falls back to an ephemeral per-example SQLite file. Either way the
property is exercised through the real runner's ``upgrade('head')`` path.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), with at least 100 iterations.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from edu_it_infrastructure.persistence.runner import Migration_Runner
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, inspect

# A disposable PostgreSQL is preferred (design Testing Strategy); when none is
# reachable the test falls back to an ephemeral SQLite file per example, which
# is how task 6.1 verified the migration lifecycle.
_EXTERNAL_DATABASE_URL = os.environ.get("EDU_IT_TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL"
)


@contextmanager
def _throwaway_database_url() -> Iterator[str]:
    """Yield a fresh, empty database URL and dispose of it afterwards.

    When an external (PostgreSQL) URL is configured it is reset to ``base`` so
    each example starts from an empty database; otherwise a brand-new temporary
    SQLite file is created and removed when the example finishes.
    """
    if _EXTERNAL_DATABASE_URL is not None:
        runner = Migration_Runner(_EXTERNAL_DATABASE_URL)
        runner.downgrade("base")
        try:
            yield _EXTERNAL_DATABASE_URL
        finally:
            runner.downgrade("base")
        return

    directory = tempfile.mkdtemp(prefix="edu_it_migrate_")
    db_path = Path(directory) / "schema.db"
    # SQLAlchemy SQLite URLs use forward slashes even on Windows.
    url = "sqlite:///" + db_path.as_posix()
    try:
        yield url
    finally:
        try:
            db_path.unlink(missing_ok=True)
        finally:
            Path(directory).rmdir()


def _schema_snapshot(database_url: str) -> dict[str, object]:
    """Capture the full user schema as a comparable, order-independent value.

    Records, for every table, its columns (name, type, nullability), primary-key
    columns, unique constraints, and foreign keys, so any schema change a second
    apply might introduce would change the snapshot. The Alembic version table is
    excluded: it tracks the migration version, not the Data_Model schema.
    """
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        snapshot: dict[str, object] = {}
        for table_name in inspector.get_table_names():
            if table_name == "alembic_version":
                continue
            columns = {
                column["name"]: (str(column["type"]), bool(column["nullable"]))
                for column in inspector.get_columns(table_name)
            }
            primary_key = tuple(
                sorted(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
            )
            unique_constraints = frozenset(
                tuple(sorted(constraint.get("column_names") or []))
                for constraint in inspector.get_unique_constraints(table_name)
            )
            foreign_keys = frozenset(
                (
                    tuple(sorted(fk.get("constrained_columns") or [])),
                    fk.get("referred_table"),
                    tuple(sorted(fk.get("referred_columns") or [])),
                )
                for fk in inspector.get_foreign_keys(table_name)
            )
            snapshot[table_name] = {
                "columns": columns,
                "primary_key": primary_key,
                "unique_constraints": unique_constraints,
                "foreign_keys": foreign_keys,
            }
        return snapshot
    finally:
        engine.dispose()


# The property quantifies over databases already at head: generate how many
# extra times migrations are re-applied to head after the first apply (1..5), so
# idempotence is checked across repeated re-applies, not just a single second
# apply.
_extra_reapplies = st.integers(min_value=1, max_value=5)


@given(extra_reapplies=_extra_reapplies)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_32_migrating_to_head_is_idempotent(extra_reapplies: int) -> None:
    """Feature: foundations, Property 32: Migrating to head is idempotent.

    After the first ``upgrade('head')`` the database is at head; each subsequent
    ``upgrade('head')`` makes no schema change — the schema snapshot stays equal
    to the one after the first apply — and the database stays at head.
    """
    with _throwaway_database_url() as database_url:
        runner = Migration_Runner(database_url)
        head = runner.head()
        assert head is not None, "the migration scripts must define a head revision"

        # First apply to head: brings the empty database to the latest version
        # and exits success (no exception).
        runner.upgrade("head")
        assert runner.current() == head
        schema_after_first = _schema_snapshot(database_url)
        # The first apply must actually create the Data_Model schema, so the
        # idempotence check below is over a non-empty schema.
        assert schema_after_first, "the first apply must create the Data_Model schema"

        # Re-applying to head one or more times is idempotent: each apply exits
        # success, the version stays at head, and the schema is unchanged from
        # the state after the first apply.
        for _ in range(extra_reapplies):
            runner.upgrade("head")
            assert runner.current() == head
            assert _schema_snapshot(database_url) == schema_after_first
