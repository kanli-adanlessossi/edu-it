"""Upgrade/rollback tests for revision ``0004_accounts_oauth_reset`` (api-route-wiring 15.1).

Req 7.9, 7.11. The revision creates only ``reset_link`` and ``pending_oauth_link``;
downgrade drops only those two and leaves every earlier table intact.
"""

from __future__ import annotations

from pathlib import Path

from edu_it_infrastructure.persistence import DATA_MODEL_TABLE_NAMES
from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect

_PREVIOUS = "0003_accounts_runtime"
_REVISION = "0004_accounts_oauth_reset"
_NEW_TABLES = frozenset({"reset_link", "pending_oauth_link"})


def _tables(url: str) -> frozenset[str]:
    engine = create_engine(url)
    try:
        return frozenset(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_creates_only_the_two_tables_and_downgrade_drops_only_them(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'oauth_reset.db'}"
    runner = Migration_Runner(url)

    runner.upgrade(_PREVIOUS)
    before = _tables(url)
    assert not _NEW_TABLES & before

    runner.upgrade(_REVISION)
    assert runner.current() == _REVISION
    after = _tables(url)
    assert after - before == _NEW_TABLES
    assert after == DATA_MODEL_TABLE_NAMES

    engine = create_engine(url)
    try:
        insp = inspect(engine)
        for name in _NEW_TABLES:
            uniques = [u["column_names"] for u in insp.get_unique_constraints(name)]
            assert ["token_digest"] in uniques
        assert {c["name"] for c in insp.get_columns("reset_link")} == {
            "id", "token_digest", "user_id", "issued_at", "expires_at", "consumed_at",
        }
        assert {c["name"] for c in insp.get_columns("pending_oauth_link")} == {
            "id", "token_digest", "provider", "subject", "user_id", "email",
            "issued_at", "expires_at", "consumed_at",
        }
    finally:
        engine.dispose()

    runner.downgrade(_PREVIOUS)
    assert runner.current() == _PREVIOUS
    assert _tables(url) == before

    runner.upgrade(_REVISION)  # re-apply after rollback succeeds
    assert _tables(url) == after

    runner.downgrade("base")
    assert _tables(url) == frozenset()
