"""Upgrade/rollback tests for revision ``0005_ai_content`` (ai-content task 3.1).

Req 23.4. The revision adds columns to ``generation_job``, ``budget_ledger``, ``variant`` and
``review_queue_item`` and creates ``variant_revision``. Runs the Migration_Runner against a
throwaway SQLite database.
"""

from __future__ import annotations

from pathlib import Path

from edu_it_infrastructure.persistence import DATA_MODEL_TABLE_NAMES
from edu_it_infrastructure.persistence.metadata import AI_CONTENT_TABLE_NAMES
from edu_it_infrastructure.persistence.runner import Migration_Runner
from sqlalchemy import create_engine, inspect, text

_PREVIOUS = "0004_accounts_oauth_reset"
_REVISION = "0005_ai_content"
_JOB_COLUMNS = {
    "stable_id",
    "kind",
    "track_stable_id",
    "stage",
    "unit_key",
    "prompt_id",
    "prompt_version",
    "cost_cap_cents",
    "branch",
    "started_at",
    "finished_at",
    "findings_json",
}
_LEDGER_COLUMNS = {
    "attempt_no",
    "model",
    "input_tokens",
    "output_tokens",
    "outcome",
    "track_stable_id",
    "recorded_at",
}
_VARIANT_COLUMNS = {
    "parent_question_stable_id",
    "track_stable_id",
    "approved_content_version",
    "withdrawn_reason",
    "created_at",
}
_STATISTIC_COLUMNS = {
    "item_kind",
    "item_stable_id",
    "sample_count",
    "correct_count",
    "timed_count",
    "total_time_ms",
    "updated_at",
}
_NEW_TABLES = {
    "variant_revision",
    "item_flag",
    "item_observation",
    "ai_cursor",
    "problem_report",
    "advisor_audit_log",
}
_RECOMMENDATION_COLUMNS = {"stable_id", "created_at", "dismissed_at", "run_id"}
_REVIEW_COLUMNS = {
    "stable_id",
    "kind",
    "target_kind",
    "target_stable_id",
    "revision_content_version",
    "track_stable_id",
    "reason",
    "comment",
    "decided_at",
    "created_at",
}


def _schema(url: str) -> tuple[frozenset[str], dict[str, set[str]]]:
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = frozenset(insp.get_table_names()) - {"alembic_version"}
        return tables, {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
    finally:
        engine.dispose()


def test_upgrade_adds_columns_and_rollback_removes_only_them(tmp_path: Path) -> None:
    """Validates: Requirements 19.1, 21.6, 23.4"""
    url = f"sqlite:///{tmp_path / 'ai.db'}"
    runner = Migration_Runner(url)
    expected_tables = DATA_MODEL_TABLE_NAMES - AI_CONTENT_TABLE_NAMES

    runner.upgrade(_PREVIOUS)
    runner.upgrade(_REVISION)
    assert runner.current() == _REVISION
    tables, cols = _schema(url)
    assert tables == DATA_MODEL_TABLE_NAMES | AI_CONTENT_TABLE_NAMES
    assert cols["generation_job"] >= _JOB_COLUMNS
    assert cols["budget_ledger"] >= _LEDGER_COLUMNS
    assert cols["variant"] >= _VARIANT_COLUMNS
    assert cols["review_queue_item"] >= _REVIEW_COLUMNS
    assert cols["item_statistic"] >= _STATISTIC_COLUMNS
    assert cols["path_recommendation"] >= _RECOMMENDATION_COLUMNS
    assert tables >= _NEW_TABLES

    runner.downgrade(_PREVIOUS)
    assert runner.current() == _PREVIOUS
    tables, cols = _schema(url)
    assert tables == expected_tables
    assert not _JOB_COLUMNS & cols["generation_job"]
    assert not _LEDGER_COLUMNS & cols["budget_ledger"]
    assert not _VARIANT_COLUMNS & cols["variant"]
    assert not _REVIEW_COLUMNS & cols["review_queue_item"]
    assert not _STATISTIC_COLUMNS & cols["item_statistic"]
    assert not _RECOMMENDATION_COLUMNS & cols["path_recommendation"]
    assert {"user_id", "source", "ordering"} <= cols["path_recommendation"]
    assert not _NEW_TABLES & tables
    assert {"correct_rate", "avg_time_ms"} <= cols["item_statistic"]
    assert {"status", "model", "tokens", "cost_cents"} <= cols["generation_job"]
    assert {"generation_job_id", "tokens", "cost_cents"} <= cols["budget_ledger"]

    runner.upgrade(_REVISION)  # re-apply after rollback succeeds
    assert _schema(url)[1]["generation_job"] >= _JOB_COLUMNS

    runner.downgrade("base")
    assert runner.current() is None
    assert _schema(url)[0] == frozenset()


def test_existing_rows_survive_and_get_a_stable_id(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    runner = Migration_Runner(url)
    runner.upgrade(_REVISION)
    runner.downgrade(_PREVIOUS)  # leaves the skeleton tables as 0001 once shaped them

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO generation_job (status, model, tokens, cost_cents) "
                    "VALUES ('done', 'm', 10, 2)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO budget_ledger (generation_job_id, tokens, cost_cents) "
                    "VALUES (1, 10, 2)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO user (id, stable_id, email, locale, status) "
                    "VALUES (1, 'usr-1', 'a@example.org', 'en', 'active')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO path_recommendation (user_id, source, ordering) "
                    "VALUES (1, 'rule-based', '[]')"
                )
            )
    finally:
        engine.dispose()

    runner.upgrade(_REVISION)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            job = conn.execute(text("SELECT stable_id, tokens FROM generation_job")).one()
            ledger = conn.execute(
                text("SELECT attempt_no, outcome, tokens FROM budget_ledger")
            ).one()
            recommendation = conn.execute(
                text("SELECT stable_id, source, dismissed_at FROM path_recommendation")
            ).one()
    finally:
        engine.dispose()
    assert job == ("job-1", 10)
    assert ledger == (1, "ok", 10)
    assert recommendation == ("rec-1", "rule-based", None)


_SEED = (
    "INSERT INTO user (id, stable_id, email, display_name, locale, status) "
    "VALUES (1, 'usr-1', 'a@example.org', 'Ada', 'fr', 'active')",
    "INSERT INTO content_provenance (id, stable_id, content_version, origin) "
    "VALUES (1, 'prov-1', 'v1', 'authored')",
    "INSERT INTO track (id, stable_id, provenance_id, title_fr, title_en, status) "
    "VALUES (1, 'trk-1', 1, 'Piste', 'Track', 'published')",
    "INSERT INTO module (id, stable_id, track_id, provenance_id, ordinal) "
    "VALUES (1, 'mod-1', 1, 1, 1)",
    "INSERT INTO lesson (id, stable_id, module_id, provenance_id, ordinal) "
    "VALUES (1, 'les-1', 1, 1, 1)",
    "INSERT INTO quiz (id, stable_id, lesson_id, provenance_id) VALUES (1, 'qz-1', 1, 1)",
    "INSERT INTO question (id, stable_id, quiz_id, provenance_id, prompt_text) "
    "VALUES (1, 'q-1', 1, 1, '?')",
    "INSERT INTO generation_job (id, status, model, tokens, cost_cents) "
    "VALUES (1, 'done', 'm', 10, 2), (2, 'failed', 'm2', 0, 0)",
    "INSERT INTO variant (id, stable_id, question_id, generation_job_id, lifecycle_state) "
    "VALUES (1, 'var-1', 1, 1, 'PendingReview')",
    "INSERT INTO review_queue_item (id, variant_id, state, reviewer_id) VALUES (1, 1, 'open', 1)",
    "INSERT INTO item_statistic (id, variant_id, correct_rate, avg_time_ms) "
    "VALUES (1, 1, 0.5, 4000.0)",
    "INSERT INTO budget_ledger (id, generation_job_id, tokens, cost_cents) "
    "VALUES (1, 1, 10, 2), (2, 2, 0, 0)",
    "INSERT INTO path_recommendation (id, user_id, source, ordering) "
    "VALUES (1, 1, 'rule-based', '[\"c1\"]')",
)
# Columns that exist in revision 0004; every one of them must survive unchanged.
_LEGACY_QUERIES = {
    "user": "SELECT id, stable_id, email, display_name, locale, status FROM user",
    "content_provenance": "SELECT id, stable_id, content_version, origin FROM content_provenance",
    "track": "SELECT id, stable_id, provenance_id, title_fr, title_en, status FROM track",
    "module": "SELECT id, stable_id, track_id, provenance_id, ordinal FROM module",
    "lesson": "SELECT id, stable_id, module_id, provenance_id, ordinal FROM lesson",
    "generation_job": "SELECT id, status, model, tokens, cost_cents FROM generation_job",
    "budget_ledger": "SELECT id, generation_job_id, tokens, cost_cents FROM budget_ledger",
    "variant": "SELECT id, stable_id, question_id, generation_job_id, lifecycle_state FROM variant",
    "review_queue_item": "SELECT id, variant_id, state, reviewer_id FROM review_queue_item",
    "item_statistic": "SELECT id, variant_id, correct_rate, avg_time_ms FROM item_statistic",
    "path_recommendation": "SELECT id, user_id, source, ordering FROM path_recommendation",
}


def _snapshot(url: str) -> dict[str, list[tuple[object, ...]]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return {
                name: [tuple(r) for r in conn.execute(text(sql)).all()]
                for name, sql in _LEGACY_QUERIES.items()
            }
    finally:
        engine.dispose()


def test_populated_database_keeps_rows_across_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
) -> None:
    """Rows from earlier revisions stay intact in both directions.

    Validates: Requirements 23.4
    """
    url = f"sqlite:///{tmp_path / 'populated.db'}"
    runner = Migration_Runner(url)
    # 0001 builds from live metadata, so round-trip once to reach the legacy table shape.
    runner.upgrade(_REVISION)
    runner.downgrade(_PREVIOUS)

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(text(statement))
    finally:
        engine.dispose()
    before = _snapshot(url)
    assert all(before.values())

    runner.upgrade(_REVISION)
    assert runner.current() == _REVISION
    assert _snapshot(url) == before

    runner.downgrade(_PREVIOUS)
    assert runner.current() == _PREVIOUS
    assert _snapshot(url) == before
    tables, cols = _schema(url)
    assert tables == DATA_MODEL_TABLE_NAMES - AI_CONTENT_TABLE_NAMES
    assert not _JOB_COLUMNS & cols["generation_job"]

    runner.upgrade(_REVISION)
    assert _snapshot(url) == before


_NEW_ROWS = (
    "INSERT INTO variant_revision (variant_id, content_version, body_json, state) "
    "VALUES (1, 'v1', '{}', 'PendingReview')",
    "INSERT INTO item_flag (item_kind, item_stable_id, reason_kind, flagged_at) "
    "VALUES ('variant', 'var-1', 'reports', '2024-01-01 00:00:00')",
    "INSERT INTO item_observation (user_id, item_kind, item_stable_id, correct, observed_at) "
    "VALUES (1, 'variant', 'var-1', 1, '2024-01-01 00:00:00')",
    "INSERT INTO ai_cursor (name, value) VALUES ('fold', 1)",
    "INSERT INTO problem_report (stable_id, user_id, item_kind, item_stable_id, "
    "content_version, category, created_at) "
    "VALUES ('rpt-1', 1, 'variant', 'var-1', 'v1', 'wrong', '2024-01-01 00:00:00')",
    "INSERT INTO advisor_audit_log (stable_id, user_id, run_id, handle, source, outcome, "
    "returned_ids_json, recorded_at) "
    "VALUES ('aud-1', 1, 'run-1', 'h', 'llm', 'accepted', '[]', '2024-01-01 00:00:00')",
)


def _structure(url: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Columns and index names per table (excluding alembic_version)."""
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names()) - {"alembic_version"}
        return (
            {t: {c["name"] for c in insp.get_columns(t)} for t in tables},
            {t: {str(i["name"]) for i in insp.get_indexes(t)} for t in tables},
        )
    finally:
        engine.dispose()


def test_downgrade_of_populated_complete_revision_removes_only_what_upgrade_added(
    tmp_path: Path,
) -> None:
    """Rows written into the new tables go with them; earlier rows and structure stay.

    Validates: Requirements 23.4
    """
    url = f"sqlite:///{tmp_path / 'complete.db'}"
    runner = Migration_Runner(url)
    runner.upgrade(_REVISION)
    runner.downgrade(_PREVIOUS)
    legacy_structure = _structure(url)

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(text(statement))
    finally:
        engine.dispose()
    before = _snapshot(url)

    runner.upgrade(_REVISION)
    full_structure = _structure(url)
    assert set(full_structure[0]) == DATA_MODEL_TABLE_NAMES | AI_CONTENT_TABLE_NAMES
    assert full_structure != legacy_structure

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in _NEW_ROWS:
                conn.execute(text(statement))
            # A task with no variant exists only from this revision on.
            conn.execute(
                text(
                    "INSERT INTO review_queue_item (id, variant_id, state, stable_id, kind) "
                    "VALUES (2, NULL, 'open', 'task-2', 'flagged_item')"
                )
            )
    finally:
        engine.dispose()

    runner.downgrade(_PREVIOUS)
    assert _structure(url) == legacy_structure
    assert _snapshot(url) == before
