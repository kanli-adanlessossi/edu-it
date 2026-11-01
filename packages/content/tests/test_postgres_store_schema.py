"""``Postgres_ContentStore`` refuses an unmigrated database and the CLI reports it (Req 12.8).

The full store contract (rows written, idempotent second sync) runs in ``apps/api/tests``, the
only package that can build the schema from the infrastructure metadata (task 7.3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edu_it_content.cli import main
from edu_it_content.postgres_store import CatalogSchemaMissingError, Postgres_ContentStore
from sqlalchemy import create_engine, text


def test_missing_tables_are_named_when_migrations_are_absent() -> None:
    engine = create_engine("sqlite://")
    with pytest.raises(CatalogSchemaMissingError) as excinfo:
        Postgres_ContentStore(engine)
    message = str(excinfo.value)
    for table in ("track", "quiz", "question_option", "rubric_criterion", "content_provenance"):
        assert table in message


def test_missing_column_is_named_when_a_later_revision_is_absent() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as cx:
        for table in (
            "track",
            "module",
            "lesson",
            "concept",
            "prerequisite",
            "quiz",
            "question",
            "question_option",
            "project",
            "rubric",
            "rubric_criterion",
            "lab",
            "lab_task",
            "lab_check",
            "content_provenance",
            "user",
            "lab_session",
        ):
            cx.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
    with pytest.raises(CatalogSchemaMissingError, match=r"track\.title_fr"):
        Postgres_ContentStore(engine)


def test_sync_with_database_url_exits_3_on_unmigrated_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "content"
    root.mkdir()
    (root / "items.json").write_text(json.dumps({"track": []}), encoding="utf-8")
    code = main(
        ["sync", "--content-root", str(root), "--database-url", f"sqlite:///{tmp_path / 'e.db'}"]
    )
    assert code == 3
    assert "track" in capsys.readouterr().err
