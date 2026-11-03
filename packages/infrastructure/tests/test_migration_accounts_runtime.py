"""Upgrade/rollback tests for revision ``0003_accounts_runtime`` (api-route-wiring 2.1).

Req 7.4, 7.9. The revision creates only ``credential`` and ``verification_link``;
downgrade drops only those two and leaves every earlier table intact.
"""

from __future__ import annotations

from pathlib import Path

from edu_it_infrastructure.persistence import DATA_MODEL_TABLE_NAMES
from edu_it_infrastructure.persistence.metadata import AI_CONTENT_TABLE_NAMES
from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect

_PREVIOUS = "0002_labs_runtime"
_REVISION = "0003_accounts_runtime"
_NEW_TABLES = frozenset({"credential", "verification_link"})


def _tables(url: str) -> frozenset[str]:
    engine = create_engine(url)
    try:
        return frozenset(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_creates_only_the_two_tables_and_downgrade_drops_only_them(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'accounts.db'}"
    runner = Migration_Runner(url)

    runner.upgrade(_PREVIOUS)
    before = _tables(url)
    assert not _NEW_TABLES & before

    runner.upgrade(_REVISION)
    assert runner.current() == _REVISION
    after = _tables(url)
    assert after - before == _NEW_TABLES
    assert after == DATA_MODEL_TABLE_NAMES - AI_CONTENT_TABLE_NAMES - {
        "reset_link",
        "pending_oauth_link",
    }

    insp_engine = create_engine(url)
    try:
        insp = inspect(insp_engine)
        credential_unique = [u["column_names"] for u in insp.get_unique_constraints("credential")]
        link_unique = [u["column_names"] for u in insp.get_unique_constraints("verification_link")]
        assert ["user_id"] in credential_unique
        assert ["token_digest"] in link_unique
        assert {c["name"] for c in insp.get_columns("verification_link")} == {
            "id",
            "token_digest",
            "user_id",
            "issued_at",
            "expires_at",
            "consumed_at",
        }
    finally:
        insp_engine.dispose()

    runner.downgrade(_PREVIOUS)
    assert runner.current() == _PREVIOUS
    assert _tables(url) == before

    runner.upgrade(_REVISION)  # re-apply after rollback succeeds
    assert _tables(url) == after

    runner.downgrade("base")
    assert _tables(url) == frozenset()
