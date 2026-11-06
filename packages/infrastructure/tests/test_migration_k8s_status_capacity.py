"""Tests for revision ``0006_k8s_status_capacity`` and the ``migrate`` entry point.

kubernetes-deployment task 3.1. Runs against a throwaway SQLite database, or against
PostgreSQL when ``TEST_DATABASE_URL`` is set (the failed-migration rollback is only
meaningful where DDL is transactional, so that test is skipped on SQLite).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest
from edu_it_infrastructure.migrate import main as migrate_main
from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect, text

_PREVIOUS = "0005_ai_content"
_REVISION = "0006_k8s_status_capacity"
_VERSIONS = (
    Path(__file__).resolve().parents[1]
    / "edu_it_infrastructure"
    / "persistence"
    / "migrations"
    / "versions"
)


def _url(tmp_path: Path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'k8s.db'}"


def _lab_session_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("lab_session")}
    finally:
        engine.dispose()


# Claims: 21.6
def test_revision_adds_capacity_attempts_and_rollback_removes_it(tmp_path: Path) -> None:
    url = _url(tmp_path)
    runner = Migration_Runner(url)
    runner.upgrade(_PREVIOUS)
    runner.upgrade("head")
    assert runner.current() == _REVISION
    assert "capacity_attempts" in _lab_session_columns(url)

    runner.downgrade(_PREVIOUS)
    assert "capacity_attempts" not in _lab_session_columns(url)
    assert "provision_attempts" in _lab_session_columns(url)


# Claims: 21.6
def test_ended_by_infrastructure_fits_the_status_column(tmp_path: Path) -> None:
    url = _url(tmp_path)
    Migration_Runner(url).upgrade("head")
    engine = create_engine(url)
    try:
        column = next(
            c for c in inspect(engine).get_columns("lab_session") if c["name"] == "status"
        )
        length = getattr(column["type"], "length", None)
        assert length is None or length >= len("ended-by-infrastructure")
        assert not inspect(engine).get_check_constraints("lab_session")
    finally:
        engine.dispose()


# Claims: 21.2, 21.3
def test_migrate_module_applies_to_head_and_exits_zero(tmp_path: Path) -> None:
    url = _url(tmp_path)
    assert migrate_main(["--database-url", url]) == 0
    assert Migration_Runner(url).current() == Migration_Runner(url).head()


# Claims: 21.2
def test_migrate_module_without_database_url_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert migrate_main([]) != 0


_FAILING = '''\
"""Throwaway revision that applies a statement, then fails."""

from alembic import op

revision = "9999_failing"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE should_not_survive (id integer)")
    op.execute("SELECT * FROM table_that_does_not_exist")


def downgrade() -> None:
    pass
'''


# Claims: 21.2, 21.3
def test_failed_migration_is_logged_exits_non_zero_and_rolls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = _url(tmp_path)
    if not url.startswith("postgresql"):
        pytest.skip("transactional DDL needs PostgreSQL (set TEST_DATABASE_URL)")
    versions = tmp_path / "versions"
    versions.mkdir()
    for source in _VERSIONS.glob("2024*.py"):
        shutil.copy2(source, versions / source.name)
    (versions / "99990101_9999_failing.py").write_text(
        _FAILING.format(head=_REVISION), encoding="utf-8"
    )

    def factory(database_url: str) -> Migration_Runner:
        return Migration_Runner(database_url, version_locations=versions)

    with caplog.at_level(logging.ERROR, logger="edu_it_infrastructure.migrate"):
        assert migrate_main(["--database-url", url], runner_factory=factory) != 0

    assert "9999_failing" in caplog.text
    assert "table_that_does_not_exist" in caplog.text
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT to_regclass('public.should_not_survive')")
            ).scalar()
        assert exists is None
    finally:
        engine.dispose()
    assert factory(url).current() == _REVISION
