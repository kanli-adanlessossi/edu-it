"""A test run must never wipe the database named by ``DATABASE_URL`` (api-route-wiring 13.5).

The Real_Stack exports ``DATABASE_URL`` for its PostgreSQL. The foundations migration property
test used to fall back to that variable and ``downgrade('base')`` it, so a verification run with
the stack up left an empty database and the next sign-up failed with ``relation "user" does not
exist`` (a 500, so the web page stayed on sign-up). Only the dedicated ``EDU_IT_TEST_DATABASE_URL``
may name a database the migration tests are allowed to reset.

Requirement 17.2, 17.3.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_FILE = "packages/infrastructure/tests/test_migration_idempotent_properties.py"


def _tables(url: str) -> frozenset[str]:
    engine = create_engine(url)
    try:
        return frozenset(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migration_property_test_leaves_database_url_schema_intact(tmp_path: Path) -> None:
    url = "sqlite:///" + (tmp_path / "live.db").as_posix()
    Migration_Runner(url).upgrade("head")
    before = _tables(url)
    assert "user" in before and "credential" in before

    env = {k: v for k, v in os.environ.items() if k != "EDU_IT_TEST_DATABASE_URL"}
    env["DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", TEST_FILE],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _tables(url) == before
