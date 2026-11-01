"""Upgrade/rollback tests for revision ``0002_labs_runtime`` (hands-on-labs task 5.3).

Req 6.1, 7.1, 11.8, 24.5. Runs the Migration_Runner against a throwaway SQLite
database. The revision is additive: it creates no table, so the table set stays
equal to the Data_Model entities before and after.
"""

from __future__ import annotations

from pathlib import Path

from edu_it_infrastructure.persistence import DATA_MODEL_TABLE_NAMES as _ALL_TABLES
from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect

_BASE = "0001_create_data_model"
_REVISION = "0002_labs_runtime"
_ACTIVE_INDEX = "uq_lab_session_active_user"
# Revisions 0003 and 0004 (later) own these four; 0001 and 0002 do not create them.
DATA_MODEL_TABLE_NAMES = _ALL_TABLES - {
    "credential",
    "verification_link",
    "reset_link",
    "pending_oauth_link",
}


def _schema(url: str) -> tuple[frozenset[str], dict[str, set[str]], set[str]]:
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = frozenset(insp.get_table_names()) - {"alembic_version"}
        cols = {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
        indexes = {i["name"] for i in insp.get_indexes("lab_session")} if tables else set()
        return tables, cols, indexes
    finally:
        engine.dispose()


def test_upgrade_adds_lab_runtime_columns_and_rollback_removes_them(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'labs.db'}"
    runner = Migration_Runner(url)

    runner.upgrade(_BASE)
    base_tables, _, _ = _schema(url)
    assert base_tables == DATA_MODEL_TABLE_NAMES

    runner.upgrade(_REVISION)
    assert runner.current() == _REVISION
    tables, cols, indexes = _schema(url)
    assert tables == DATA_MODEL_TABLE_NAMES  # additive: no table added (Req 24.5)
    assert {"accepted_at", "deadline_at", "stable_id", "started_at", "results"} <= cols["lab_run"]
    assert {"sandbox_ref", "terminal_epoch", "provision_attempts"} <= cols["lab_session"]
    assert "counted_for_mastery" in cols["lab_task_completion"]
    assert "lab_task_id" in cols["hint_reveal"]
    assert _ACTIVE_INDEX in indexes

    runner.downgrade(_BASE)
    assert runner.current() == _BASE
    tables, cols, indexes = _schema(url)
    assert tables == DATA_MODEL_TABLE_NAMES
    assert not {"accepted_at", "deadline_at", "stable_id"} & cols["lab_run"]
    assert "lab_task_id" not in cols["hint_reveal"]
    assert _ACTIVE_INDEX not in indexes

    runner.upgrade(_REVISION)  # re-apply after rollback succeeds
    assert _schema(url)[0] == DATA_MODEL_TABLE_NAMES

    runner.downgrade("base")
    assert runner.current() is None
    assert _schema(url)[0] == frozenset()
