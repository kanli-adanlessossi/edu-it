"""Hands-on labs runtime columns (additive only).

Revision ID: 0002_labs_runtime
Revises: 0001_create_data_model
Create Date: 2024-01-02 00:00:00

Adds the columns, the partial unique index, and the check constraint the lab
runtime needs (design "Persistence"; Req 6.1, 7.1, 11.4, 11.8, 15.3, 19.1, 24.4).
No table is created or dropped and no existing column is removed, so the
export/deletion/retention coverage of D3 Section 13 is unchanged.

``0001`` creates tables from the live ``metadata`` object, so a database built
from scratch already has these columns when ``0002`` runs. Every step therefore
inspects the live schema first and only adds what is missing; on a database
created by an older ``0001`` the steps add the columns. ``downgrade`` removes
only what ``upgrade`` added.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from edu_it_infrastructure.persistence.metadata import metadata

# revision identifiers, used by Alembic.
revision: str = "0002_labs_runtime"
down_revision: str | None = "0001_create_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIVE_USER_INDEX = "uq_lab_session_active_user"
_ACTIVE_PREDICATE = "status in ('provisioning', 'ready', 'grading')"
_HINT_CHECK = "hint_reveal_exactly_one_target"
_HINT_CHECK_SQL = (
    "(question_id IS NOT NULL AND lab_task_id IS NULL) OR "
    "(question_id IS NULL AND lab_task_id IS NOT NULL)"
)
_RUN_STABLE_UQ = "uq_lab_run_stable_id"
_HINT_TASK_FK = "fk_hint_reveal_lab_task_id_lab_task"

# (table, column) additions that are safe on a populated table.
_SIMPLE_COLUMNS: tuple[tuple[str, sa.Column], ...] = (  # type: ignore[type-arg]
    ("lab", sa.Column("kind", sa.String(16), nullable=False, server_default="terminal")),
    ("lab", sa.Column("image", sa.String(255))),
    ("lab", sa.Column("concept_stable_ids", sa.Text())),
    ("lab", sa.Column("archived_at", sa.DateTime(timezone=True))),
    ("lab_task", sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true())),
    ("lab_check", sa.Column("timeout_seconds", sa.Integer())),
    ("lab_check", sa.Column("script", sa.Text())),
    ("lab_session", sa.Column("sandbox_ref", sa.String(255))),
    ("lab_session", sa.Column("last_activity_at", sa.DateTime(timezone=True))),
    ("lab_session", sa.Column("last_grading_accepted_at", sa.DateTime(timezone=True))),
    ("lab_session", sa.Column("ended_at", sa.DateTime(timezone=True))),
    (
        "lab_session",
        sa.Column("provision_attempts", sa.Integer(), nullable=False, server_default="0"),
    ),
    ("lab_session", sa.Column("next_provision_at", sa.DateTime(timezone=True))),
    ("lab_session", sa.Column("terminal_epoch", sa.Integer(), nullable=False, server_default="0")),
    ("lab_run", sa.Column("started_at", sa.DateTime(timezone=True))),
    ("lab_run", sa.Column("results", sa.Text())),
    (
        "lab_task_completion",
        sa.Column("counted_for_mastery", sa.Boolean(), nullable=False, server_default=sa.false()),
    ),
)


def _columns(table: str) -> dict[str, dict]:  # type: ignore[type-arg]
    return {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns(table)}


def _batch(table: str):  # noqa: ANN202
    return op.batch_alter_table(table, naming_convention=metadata.naming_convention)


def upgrade() -> None:
    """Add the lab runtime columns, index, and constraint where missing."""
    for table, column in _SIMPLE_COLUMNS:
        if column.name not in _columns(table):
            op.add_column(table, column.copy())

    _upgrade_lab_run()
    _upgrade_hint_reveal()

    inspector = sa.inspect(op.get_bind())
    if _ACTIVE_USER_INDEX not in {i["name"] for i in inspector.get_indexes("lab_session")}:
        op.create_index(
            _ACTIVE_USER_INDEX,
            "lab_session",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text(_ACTIVE_PREDICATE),
            sqlite_where=sa.text(_ACTIVE_PREDICATE),
        )


def _upgrade_lab_run() -> None:
    cols = _columns("lab_run")
    if "stable_id" in cols:
        return  # created from the current metadata; already complete
    op.add_column("lab_run", sa.Column("stable_id", sa.String(64)))
    op.add_column("lab_run", sa.Column("accepted_at", sa.DateTime(timezone=True)))
    op.add_column("lab_run", sa.Column("deadline_at", sa.DateTime(timezone=True)))
    # Backfill any pre-existing rows so the NOT NULL tightening below succeeds.
    op.execute(
        "UPDATE lab_run SET stable_id = 'run-' || CAST(id AS VARCHAR), "
        "accepted_at = COALESCE(graded_at, CURRENT_TIMESTAMP), "
        "deadline_at = COALESCE(graded_at, CURRENT_TIMESTAMP)"
    )
    with _batch("lab_run") as batch:
        batch.alter_column("stable_id", existing_type=sa.String(64), nullable=False)
        batch.alter_column("accepted_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("deadline_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_unique_constraint(_RUN_STABLE_UQ, ["stable_id"])


def _upgrade_hint_reveal() -> None:
    if "lab_task_id" in _columns("hint_reveal"):
        return  # created from the current metadata; already complete
    with _batch("hint_reveal") as batch:
        batch.add_column(sa.Column("lab_task_id", sa.Integer()))
        batch.create_foreign_key(_HINT_TASK_FK, "lab_task", ["lab_task_id"], ["id"])
        batch.alter_column("question_id", existing_type=sa.Integer(), nullable=True)
        batch.create_check_constraint(_HINT_CHECK, _HINT_CHECK_SQL)


def downgrade() -> None:
    """Remove exactly what :func:`upgrade` added."""
    op.drop_index(_ACTIVE_USER_INDEX, table_name="lab_session")

    # Lab hints cannot exist without lab_task_id; drop them before restoring NOT NULL.
    op.execute("DELETE FROM hint_reveal WHERE question_id IS NULL")
    with _batch("hint_reveal") as batch:
        batch.drop_constraint(_HINT_CHECK, type_="check")
        batch.drop_constraint(_HINT_TASK_FK, type_="foreignkey")
        batch.drop_column("lab_task_id")
        batch.alter_column("question_id", existing_type=sa.Integer(), nullable=False)

    with _batch("lab_run") as batch:
        batch.drop_constraint(_RUN_STABLE_UQ, type_="unique")
        for name in ("results", "started_at", "deadline_at", "accepted_at", "stable_id"):
            batch.drop_column(name)

    for table, column in reversed(_SIMPLE_COLUMNS):
        if table == "lab_run":
            continue
        with _batch(table) as batch:
            batch.drop_column(column.name)
