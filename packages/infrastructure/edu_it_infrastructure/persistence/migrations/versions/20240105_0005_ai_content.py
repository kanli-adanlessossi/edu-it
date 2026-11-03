"""AI content generation and budget columns (additive only).

Revision ID: 0005_ai_content
Revises: 0004_accounts_oauth_reset
Create Date: 2024-01-05 00:00:00

Adds the columns the generation pipeline needs on the skeleton tables
``generation_job`` and ``budget_ledger``, and the review columns on ``variant`` and
``review_queue_item`` (ai-content design "Data Models"; Req 23.4). It also creates
the tables listed in ``AI_CONTENT_TABLE_NAMES`` (``variant_revision``, ``item_flag``,
``item_observation``, ``ai_cursor``, ``problem_report`` and ``advisor_audit_log`` so far) and adds
the quality columns to ``item_statistic`` and the recommendation columns to
``path_recommendation``; later ai-content tasks extend this same revision with the
tables they introduce.

Like ``0002_labs_runtime``, every step inspects the live schema first and only
adds what is missing: ``0001`` builds tables from the live ``metadata`` object, so
a database created from scratch already has these columns. Existing rows survive
(``stable_id`` is backfilled before it is made NOT NULL and unique) and
``downgrade`` removes only what ``upgrade`` added.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import cast

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations
from sqlalchemy.engine.interfaces import ReflectedColumn

from edu_it_infrastructure.persistence.metadata import AI_CONTENT_TABLE_NAMES, metadata

# revision identifiers, used by Alembic.
revision: str = "0005_ai_content"
down_revision: str | None = "0004_accounts_oauth_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_STABLE_UQ = "uq_generation_job_stable_id"
_REVIEW_STABLE_UQ = "uq_review_queue_item_stable_id"
_RECOMMENDATION_STABLE_UQ = "uq_path_recommendation_stable_id"

# Columns added to the skeleton tables, other than the unique ``generation_job.stable_id``.
# Definitions are taken from the live metadata so the two can never drift.
_JOB_COLUMNS: tuple[str, ...] = (
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
)
_LEDGER_COLUMNS: tuple[str, ...] = (
    "attempt_no",
    "model",
    "input_tokens",
    "output_tokens",
    "outcome",
    "track_stable_id",
    "recorded_at",
)
_VARIANT_COLUMNS: tuple[str, ...] = (
    "parent_question_stable_id",
    "track_stable_id",
    "approved_content_version",
    "withdrawn_reason",
    "created_at",
)
_REVIEW_COLUMNS: tuple[str, ...] = (
    "kind",
    "target_kind",
    "target_stable_id",
    "revision_content_version",
    "track_stable_id",
    "reason",
    "comment",
    "decided_at",
    "created_at",
)
_STATISTIC_COLUMNS: tuple[str, ...] = (
    "item_kind",
    "item_stable_id",
    "sample_count",
    "correct_count",
    "timed_count",
    "total_time_ms",
    "updated_at",
)
_RECOMMENDATION_COLUMNS: tuple[str, ...] = ("created_at", "dismissed_at", "run_id")
_SIMPLE_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    [("generation_job", name) for name in _JOB_COLUMNS]
    + [("budget_ledger", name) for name in _LEDGER_COLUMNS]
    + [("variant", name) for name in _VARIANT_COLUMNS]
    + [("review_queue_item", name) for name in _REVIEW_COLUMNS]
    + [("item_statistic", name) for name in _STATISTIC_COLUMNS]
    + [("path_recommendation", name) for name in _RECOMMENDATION_COLUMNS]
)
# Indexes added to the skeleton tables, with the table that owns each.
_INDEXES: tuple[tuple[str, str], ...] = (
    ("variant", "ix_variant_parent_state"),
    ("review_queue_item", "ix_review_queue_item_page"),
    ("review_queue_item", "ix_review_queue_item_target"),
    ("item_statistic", "uq_item_statistic_item"),
    ("path_recommendation", "ix_path_recommendation_user_created"),
    ("path_recommendation", "ix_path_recommendation_created_at"),
)


def _columns(table: str) -> dict[str, ReflectedColumn]:
    return {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _batch(table: str) -> AbstractContextManager[BatchOperations]:
    convention = cast("dict[str, str]", dict(metadata.naming_convention))
    return op.batch_alter_table(table, naming_convention=convention)


def upgrade() -> None:
    """Add the generation and budget columns where missing."""
    for table, name in _SIMPLE_COLUMNS:
        if name not in _columns(table):
            op.add_column(table, metadata.tables[table].c[name].copy())

    _upgrade_job_stable_id()
    _upgrade_review_queue_item()
    _upgrade_path_recommendation_stable_id()

    for table, index_name in _INDEXES:
        if index_name not in _index_names(table):
            index = next(i for i in metadata.tables[table].indexes if i.name == index_name)
            op.create_index(
                index_name, table, [c.name for c in index.columns], unique=bool(index.unique)
            )

    new_tables = [metadata.tables[n] for n in sorted(AI_CONTENT_TABLE_NAMES)]
    if new_tables:
        metadata.create_all(bind=op.get_bind(), tables=new_tables, checkfirst=True)


def _upgrade_job_stable_id() -> None:
    if "stable_id" in _columns("generation_job"):
        return  # created from the current metadata; already complete
    op.add_column("generation_job", sa.Column("stable_id", sa.String(64)))
    # Backfill any pre-existing rows so the NOT NULL tightening below succeeds.
    op.execute("UPDATE generation_job SET stable_id = 'job-' || CAST(id AS VARCHAR)")
    with _batch("generation_job") as batch:
        batch.alter_column("stable_id", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint(_JOB_STABLE_UQ, ["stable_id"])


def _upgrade_path_recommendation_stable_id() -> None:
    if "stable_id" in _columns("path_recommendation"):
        return  # created from the current metadata; already complete
    op.add_column("path_recommendation", sa.Column("stable_id", sa.String(64)))
    # Backfill any pre-existing rows so the NOT NULL tightening below succeeds.
    op.execute("UPDATE path_recommendation SET stable_id = 'rec-' || CAST(id AS VARCHAR)")
    with _batch("path_recommendation") as batch:
        batch.alter_column("stable_id", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint(_RECOMMENDATION_STABLE_UQ, ["stable_id"])


def _upgrade_review_queue_item() -> None:
    columns = _columns("review_queue_item")
    if "stable_id" in columns:
        return  # created from the current metadata; already complete
    op.add_column("review_queue_item", sa.Column("stable_id", sa.String(64)))
    # Backfill any pre-existing rows so the NOT NULL tightening below succeeds.
    op.execute("UPDATE review_queue_item SET stable_id = 'task-' || CAST(id AS VARCHAR)")
    with _batch("review_queue_item") as batch:
        batch.alter_column("stable_id", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint(_REVIEW_STABLE_UQ, ["stable_id"])
        # A flagged_item task can target a question, lesson or lab, not only a variant.
        batch.alter_column("variant_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Remove exactly what :func:`upgrade` added."""
    new_tables = [metadata.tables[n] for n in sorted(AI_CONTENT_TABLE_NAMES)]
    if new_tables:
        metadata.drop_all(bind=op.get_bind(), tables=list(reversed(new_tables)), checkfirst=True)

    for table, index_name in reversed(_INDEXES):
        if index_name in _index_names(table):
            op.drop_index(index_name, table_name=table)

    # Tasks without a variant cannot exist before this revision; drop them so the
    # NOT NULL constraint on ``variant_id`` can be restored.
    op.execute("DELETE FROM review_queue_item WHERE variant_id IS NULL")
    with _batch("review_queue_item") as batch:
        batch.drop_constraint(_REVIEW_STABLE_UQ, type_="unique")
        batch.alter_column("variant_id", existing_type=sa.Integer(), nullable=False)
        for name in (*reversed(_REVIEW_COLUMNS), "stable_id"):
            batch.drop_column(name)

    with _batch("path_recommendation") as batch:
        batch.drop_constraint(_RECOMMENDATION_STABLE_UQ, type_="unique")
        for name in (*reversed(_RECOMMENDATION_COLUMNS), "stable_id"):
            batch.drop_column(name)

    with _batch("item_statistic") as batch:
        for name in reversed(_STATISTIC_COLUMNS):
            batch.drop_column(name)

    with _batch("variant") as batch:
        for name in reversed(_VARIANT_COLUMNS):
            batch.drop_column(name)

    with _batch("budget_ledger") as batch:
        for name in reversed(_LEDGER_COLUMNS):
            batch.drop_column(name)

    with _batch("generation_job") as batch:
        batch.drop_constraint(_JOB_STABLE_UQ, type_="unique")
        for name in (*reversed(_JOB_COLUMNS), "stable_id"):
            batch.drop_column(name)
