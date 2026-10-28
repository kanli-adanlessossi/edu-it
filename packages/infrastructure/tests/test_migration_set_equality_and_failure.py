"""Unit tests for the data-model migrations (task 6.5).

Two behaviors are asserted here as worked examples, complementing the
property-based migration tests (tasks 6.2-6.4):

1. **Set equality (Req 4.1, 4.2).** Applying all migrations to an empty database
   creates *exactly one table per Data_Model entity* — no listed entity omitted
   and no table beyond those entities. The expected entity set is spelled out
   **explicitly** from ``docs/DATA-MODEL.md`` (D3) Section 2 ("Entity Groups at a
   Glance") and the ER diagram, rather than being derived from
   :data:`~edu_it_infrastructure.persistence.metadata.DATA_MODEL_TABLE_NAMES`.
   Deriving the expectation from the code under test would make the assertion
   circular and blind to drift; stating it independently lets the test genuinely
   catch an entity that is added, dropped, or renamed in the schema without a
   matching change to the data model document.

2. **Mid-migration failure (Req 4.5).** When a migration fails partway through
   application, the runner rolls back the failing migration, leaves the database
   at the last successfully committed version, raises
   :class:`~edu_it_infrastructure.persistence.runner.MigrationFailed` naming the
   failing revision, and the ``edu-it-migrate`` CLI exits non-zero reporting the
   failing migration. We simulate the failure by injecting a throwaway revision
   that chains after ``0001`` and raises during ``upgrade()``; the temporary
   revision artifact is removed afterward.

No live PostgreSQL is provisioned in this phase, so (following task 6.1's
lifecycle verification) these tests run against a throwaway SQLite database if a
live PostgreSQL is not reachable.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from edu_it_infrastructure.persistence.metadata import DATA_MODEL_TABLE_NAMES
from edu_it_infrastructure.persistence.runner import (
    Migration_Runner,
    MigrationFailed,
    main,
)
from sqlalchemy import create_engine, inspect

# ---------------------------------------------------------------------------
# The authoritative Data_Model entity set, transcribed BY HAND from
# docs/DATA-MODEL.md (D3) Section 2 ("Entity Groups at a Glance") and the ER
# diagram in Section 3. This is deliberately NOT built from the schema under
# test (DATA_MODEL_TABLE_NAMES / metadata) so the set-equality test detects
# drift: if a table is added, removed, or renamed in the schema without a
# corresponding change to the data model document, one of the two directions of
# the set comparison fails.
# ---------------------------------------------------------------------------

_EXPECTED_ENTITIES_BY_GROUP: dict[str, frozenset[str]] = {
    # Identity (D3 Section 2, 4 entities)
    "identity": frozenset(
        {
            "user",
            "session",
            "oauth_link",
            "role_assignment",
        }
    ),
    # Catalog (D3 Section 2, 14 entities)
    "catalog": frozenset(
        {
            "track",
            "module",
            "lesson",
            "lab",
            "lab_task",
            "lab_check",
            "project",
            "rubric",
            "rubric_criterion",
            "quiz",
            "question",
            "question_option",
            "concept",
            "prerequisite",
        }
    ),
    # Provenance (D3 Section 2, 1 entity)
    "provenance": frozenset({"content_provenance"}),
    # Progress (D3 Section 2, 9 entities)
    "progress": frozenset(
        {
            "enrollment",
            "module_progress",
            "lesson_completion",
            "lab_session",
            "lab_run",
            "lab_task_completion",
            "quiz_attempt",
            "answer",
            "project_submission",
        }
    ),
    # Mastery and review (D3 Section 2, 5 entities)
    "mastery_and_review": frozenset(
        {
            "mastery",
            "mastery_event",
            "review_schedule",
            "review_attempt",
            "hint_reveal",
        }
    ),
    # AI / review ops (D3 Section 2, 6 entities)
    "ai_review_ops": frozenset(
        {
            "review_queue_item",
            "variant",
            "generation_job",
            "item_statistic",
            "path_recommendation",
            "budget_ledger",
        }
    ),
    # Assessments (D3 Section 2, 2 entities)
    "assessments": frozenset(
        {
            "placement",
            "final_assessment",
        }
    ),
}

# The flattened expected set: the union of every group above (41 entities).
EXPECTED_DATA_MODEL_ENTITIES: frozenset[str] = frozenset().union(
    *_EXPECTED_ENTITIES_BY_GROUP.values()
)

# D3 states the model has 41 entities with no join tables beyond those listed; a
# guard so the hand-written set below cannot silently gain or lose a name (e.g.
# via a copy/paste duplicate across groups) without this count check catching it.
_EXPECTED_ENTITY_COUNT = 41


def _test_database_url(tmp_path: Path) -> str:
    """Return the database URL for a lifecycle test.

    Prefers a live PostgreSQL via ``TEST_DATABASE_URL`` when one is provisioned;
    otherwise falls back to a throwaway SQLite file, mirroring task 6.1's
    lifecycle verification when no PostgreSQL is reachable.
    """
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        return configured
    return f"sqlite:///{tmp_path / 'edu_it_migration_test.sqlite'}"


def _created_table_names(database_url: str) -> frozenset[str]:
    """Reflect the names of the tables that exist in ``database_url``.

    Excludes Alembic's own ``alembic_version`` bookkeeping table, which is not a
    Data_Model entity.
    """
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        return frozenset(inspector.get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Set equality: exactly one table per Data_Model entity (Req 4.1, 4.2)
# ---------------------------------------------------------------------------


def test_expected_entity_set_is_the_documented_41_with_no_duplicates() -> None:
    # Guards the hand-transcribed expectation itself: the groups partition into
    # exactly the 41 entities D3 declares, with no name repeated across groups.
    total_named = sum(len(group) for group in _EXPECTED_ENTITIES_BY_GROUP.values())
    assert total_named == _EXPECTED_ENTITY_COUNT, (
        "the per-group entity lists transcribed from D3 Section 2 contain a "
        f"duplicate or miscount: {total_named} names across groups, expected "
        f"{_EXPECTED_ENTITY_COUNT}"
    )
    assert len(EXPECTED_DATA_MODEL_ENTITIES) == _EXPECTED_ENTITY_COUNT


def test_migration_creates_exactly_one_table_per_data_model_entity(tmp_path: Path) -> None:
    # Req 4.1, 4.2: applying all migrations to an empty database creates exactly
    # one table per Data_Model entity — no listed entity omitted, no extra table.
    database_url = _test_database_url(tmp_path)
    runner = Migration_Runner(database_url)
    runner.upgrade("head")

    created = _created_table_names(database_url)

    missing = EXPECTED_DATA_MODEL_ENTITIES - created
    extra = created - EXPECTED_DATA_MODEL_ENTITIES
    assert not missing, f"Data_Model entities with no created table (Req 4.1): {sorted(missing)}"
    assert not extra, f"tables created beyond the Data_Model entities (Req 4.1): {sorted(extra)}"
    # Set equality: the created tables are exactly the documented entities.
    assert created == EXPECTED_DATA_MODEL_ENTITIES


def test_schema_metadata_matches_the_documented_entities() -> None:
    # The schema's own derived name set must equal the independently transcribed
    # D3 entity set. This ties DATA_MODEL_TABLE_NAMES (derived from the MetaData)
    # to the authoritative document without the migration test relying on it.
    assert DATA_MODEL_TABLE_NAMES == EXPECTED_DATA_MODEL_ENTITIES


# ---------------------------------------------------------------------------
# 2. Mid-migration failure leaves the DB at the last committed version (Req 4.5)
# ---------------------------------------------------------------------------

_FAILING_REVISION = "9999_failing_test_migration"
_FAILING_REVISION_FILENAME = "99990101_9999_failing_test_migration.py"
_FAILING_MESSAGE = "intentional mid-migration failure for task 6.5"

# The real, committed create-data-model revision. The isolated history the
# failing-migration tests build is "apply 0001, then attempt the failing
# revision chained after it", so the throwaway versions directory needs a copy
# of this script too.
_REAL_VERSIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "edu_it_infrastructure"
    / "persistence"
    / "migrations"
    / "versions"
)
_REAL_HEAD_FILENAME = "20240101_0001_create_data_model.py"

# A throwaway Alembic revision that chains after 0001 and raises during upgrade,
# simulating a migration that fails partway through application (Req 4.5). Its
# down_revision points at 0001 so, after the failing migration's own
# transaction rolls back, the database is left at 0001 (the last committed
# version).
_FAILING_REVISION_SOURCE = f'''\
"""Throwaway failing revision used by test_migration_set_equality_and_failure.

Revision ID: {_FAILING_REVISION}
Revises: 0001_create_data_model
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "{_FAILING_REVISION}"
down_revision: str | None = "0001_create_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    raise RuntimeError("{_FAILING_MESSAGE}")


def downgrade() -> None:
    pass
'''


@pytest.fixture
def isolated_failing_versions_dir(tmp_path: Path) -> Path:
    """Build an isolated versions directory whose head is a failing revision.

    Copies the real ``0001_create_data_model`` script into a fresh temp
    directory and writes the throwaway failing revision alongside it (chained
    after ``0001``). Returning this directory lets a ``Migration_Runner`` target
    the alternate history via its ``version_locations`` override **without
    touching the shared package ``versions/`` directory** ? which would
    otherwise change the ambient Alembic ``head`` seen by every other test in
    the suite and make them flaky. ``tmp_path`` is cleaned up by pytest, so no
    teardown is needed.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    # Copy the committed head so the isolated history starts from a real 0001.
    shutil.copy2(_REAL_VERSIONS_DIR / _REAL_HEAD_FILENAME, versions_dir / _REAL_HEAD_FILENAME)
    # Chain the deliberately failing revision after it.
    (versions_dir / _FAILING_REVISION_FILENAME).write_text(
        _FAILING_REVISION_SOURCE, encoding="utf-8"
    )
    return versions_dir


def test_mid_migration_failure_leaves_db_at_last_committed_version(
    tmp_path: Path, isolated_failing_versions_dir: Path
) -> None:
    # Req 4.5: a migration that fails partway is rolled back, the database is
    # left at the last successfully committed version, and MigrationFailed names
    # the failing revision.
    database_url = _test_database_url(tmp_path)
    # Target the isolated history (0001 -> failing revision) via the runner's
    # version_locations override; the shared package directory is untouched.
    runner = Migration_Runner(database_url, version_locations=isolated_failing_versions_dir)
    assert runner.head() == _FAILING_REVISION

    with pytest.raises(MigrationFailed) as exc_info:
        runner.upgrade("head")

    failure = exc_info.value
    # The failing migration is reported by revision.
    assert failure.revision == _FAILING_REVISION
    # The database is left at the last successfully committed version (0001),
    # not at the failing head and not empty.
    assert failure.at_version == "0001_create_data_model"
    assert runner.current() == "0001_create_data_model"
    # The committed 0001 schema is intact: the Data_Model tables exist.
    created = _created_table_names(database_url)
    assert created == EXPECTED_DATA_MODEL_ENTITIES


def test_cli_exits_non_zero_and_reports_the_failing_migration(
    tmp_path: Path,
    isolated_failing_versions_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Req 4.5: the edu-it-migrate CLI exits non-zero on a mid-migration failure
    # and reports the failing migration.
    database_url = _test_database_url(tmp_path)

    # Inject a runner factory bound to the isolated failing history so the CLI
    # exercises the mid-migration failure without the shared package directory
    # being mutated; the public CLI flags are unchanged.
    def runner_factory(url: str) -> Migration_Runner:
        return Migration_Runner(url, version_locations=isolated_failing_versions_dir)

    exit_code = main(
        ["upgrade", "--database-url", database_url],
        runner_factory=runner_factory,
    )

    # Non-zero exit on mid-migration failure.
    assert exit_code != 0
    # The failing migration is reported (on stderr), naming the failing revision
    # and the last committed version it rolled back to.
    stderr = capsys.readouterr().err
    assert _FAILING_REVISION in stderr
    assert "0001_create_data_model" in stderr
    assert re.search(r"migration failed", stderr)

    # The database is left at the last committed version, confirmed via a fresh
    # runner reading the current version.
    assert (
        Migration_Runner(database_url, version_locations=isolated_failing_versions_dir).current()
        == "0001_create_data_model"
    )
