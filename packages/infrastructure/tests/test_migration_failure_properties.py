"""Property test for failed migrations (kubernetes-deployment task 3.2).

Feature: kubernetes-deployment, Property 32: Failed migration leaves the schema unchanged

Property 32 (design.md): *For any* migration that fails at any statement, the
``python -m edu_it_infrastructure.migrate`` job exits non-zero, logs the failing
migration identifier and the database error text, and the database schema (tables
and columns) is identical to the schema before that migration started.

Validates: Requirements 21.2, 21.3

Where it runs:

- PostgreSQL (``TEST_DATABASE_URL`` set): the failing statement is drawn at any
  position, after 0..4 statements that succeed (CREATE TABLE / ADD COLUMN). This is the
  full property, because PostgreSQL DDL is transactional and the migration's single
  transaction rolls the earlier statements back (Req 21.3).
- SQLite (default): the driver does not make DDL transactional, so statements that
  succeed before the failure cannot be rolled back. Only the position-0 case (the
  failure is the first statement) is checked. It still covers the exit status, the
  logged identifier and error text, the unchanged schema and the unchanged version.
  The mid-migration rollback is NOT verified on SQLite.

The migration under test is a throwaway revision chained after the real head, in an
isolated ``version_locations`` directory, so the shared ``versions/`` is never touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from edu_it_infrastructure.migrate import main as migrate_main
from edu_it_infrastructure.persistence.runner import Migration_Runner
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, inspect, text

_VERSIONS = (
    Path(__file__).resolve().parents[1]
    / "edu_it_infrastructure"
    / "persistence"
    / "migrations"
    / "versions"
)
_FAILING_REVISION = "9999_prop32_failing"
_PG_URL = os.environ.get("TEST_DATABASE_URL")
_ON_POSTGRES = bool(_PG_URL and _PG_URL.startswith("postgresql"))

_SOURCE = '''\
"""Throwaway revision for Property 32."""

from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = "{head}"
branch_labels = None
depends_on = None

_STATEMENTS = {statements!r}


def upgrade() -> None:
    for statement in _STATEMENTS:
        if statement.startswith("RAISE "):
            raise RuntimeError(statement[len("RAISE "):])
        op.execute(sa.text(statement))


def downgrade() -> None:
    pass
'''

# (failing statement, text the database error must contain, case-insensitive)
_FAILURES = [
    ("SELECT * FROM prop32_missing_table", "prop32_missing_table"),
    ("CREATE TABLE lab_session (id integer)", "already exists"),
    ("ALTER TABLE lab_session ADD COLUMN status integer", "status"),
    ("THIS IS NOT SQL prop32", "syntax error"),
    ("RAISE prop32 python-level failure", "prop32 python-level failure"),
]

_GOOD = [
    "CREATE TABLE prop32_t{n} (id integer, note text)",
    "ALTER TABLE lab_session ADD COLUMN prop32_c{n} integer",
]


def _snapshot(url: str) -> dict[str, list[tuple[str, str]]]:
    """Tables and their (column name, type) pairs, ignoring Alembic bookkeeping."""
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        return {
            table: sorted((c["name"], str(c["type"])) for c in inspector.get_columns(table))
            for table in inspector.get_table_names()
            if table != "alembic_version"
        }
    finally:
        engine.dispose()


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _drop_leftovers(url: str) -> None:
    """Remove anything a (buggy) partial run could leave behind on a shared database."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for n in range(8):
                connection.execute(text(f"DROP TABLE IF EXISTS prop32_t{n}"))
                if _ON_POSTGRES:
                    connection.execute(
                        text(f"ALTER TABLE lab_session DROP COLUMN IF EXISTS prop32_c{n}")
                    )
    finally:
        engine.dispose()


# Claims: 21.2, 21.3
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(data=st.data())
def test_property_32_failed_migration_leaves_schema_unchanged(data: st.DataObject) -> None:
    """Property 32: Failed migration leaves the schema unchanged.

    Feature: kubernetes-deployment, Property 32: Failed migration leaves the schema
    unchanged. Validates: Requirements 21.2, 21.3.
    """
    before_count = data.draw(st.integers(0, 4 if _ON_POSTGRES else 0))
    after_count = data.draw(st.integers(0, 2))
    failing_sql, marker = data.draw(st.sampled_from(_FAILURES))
    statements = (
        [data.draw(st.sampled_from(_GOOD)).format(n=n) for n in range(before_count)]
        + [failing_sql]
        + ["CREATE TABLE prop32_t7 (id integer)"] * after_count
    )
    # Duplicate-name collisions among the good statements are avoided by index n.

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        url = _PG_URL if _ON_POSTGRES else f"sqlite:///{tmp_path / 'p32.db'}"
        assert url is not None
        versions = tmp_path / "versions"
        versions.mkdir()
        for source in _VERSIONS.glob("2024*.py"):
            shutil.copy2(source, versions / source.name)
        real_head = Migration_Runner(url, version_locations=versions).head()
        assert real_head is not None
        (versions / "99990101_9999_prop32_failing.py").write_text(
            _SOURCE.format(revision=_FAILING_REVISION, head=real_head, statements=statements),
            encoding="utf-8",
        )

        def factory(database_url: str) -> Migration_Runner:
            return Migration_Runner(database_url, version_locations=versions)

        capture = _Capture()
        logger = logging.getLogger("edu_it_infrastructure.migrate")
        try:
            # Bring the database to the pre-failure state: every real migration applied.
            Migration_Runner(url, version_locations=versions).upgrade(real_head)
            schema_before = _snapshot(url)

            logger.addHandler(capture)
            try:
                exit_code = migrate_main(["--database-url", url], runner_factory=factory)
            finally:
                logger.removeHandler(capture)

            assert exit_code != 0  # Req 21.2
            logged = "\n".join(capture.messages)
            assert _FAILING_REVISION in logged  # migration id logged (Req 21.2)
            assert marker.lower() in logged.lower()  # database error text logged (Req 21.2)
            assert _snapshot(url) == schema_before  # Req 21.3
            assert factory(url).current() == real_head  # version marker not advanced
        finally:
            if _ON_POSTGRES:
                _drop_leftovers(url)
