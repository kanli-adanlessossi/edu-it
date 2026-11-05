"""Kubernetes deployment: infrastructure-ended status and capacity attempts (additive only).

Revision ID: 0006_k8s_status_capacity
Revises: 0005_ai_content
Create Date: 2024-01-06 00:00:00

kubernetes-deployment Req 21.6, 22.4, 22.5. Two additions, both safe for the previous
release to run against:

* The ``ended-by-infrastructure`` Lab_Session status. ``lab_session.status`` is a plain
  ``VARCHAR(32)`` with no CHECK constraint, and the new value (23 characters) fits, so
  no DDL is needed. The new release is the only writer of the value; reads in the
  previous release tolerate an unknown status (see ``Postgres_LabSessionRepository``).
  The partial unique index on active sessions lists only active statuses, so the
  terminal value never holds a learner's active-session slot.
* ``lab_session.capacity_attempts``: the count of provisioning attempts of kind
  ``capacity``. ``provision_attempts`` keeps counting the other kinds, so the retry
  allowance counts only attempts of other kinds. The column is NOT NULL with a
  default of 0, so the previous release's inserts keep working.

Like ``0002_labs_runtime``, the step inspects the live schema first: ``0001`` builds
tables from the live ``metadata``, so a database created from scratch already has the
column. ``downgrade`` removes only what ``upgrade`` added; it is not used by an upgrade
and would discard capacity counts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_k8s_status_capacity"
down_revision: str | None = "0005_ai_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lab_session"
_COLUMN = "capacity_attempts"


def _has_column() -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    """Add ``lab_session.capacity_attempts`` where missing."""
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    """Remove exactly what :func:`upgrade` added."""
    if _has_column():
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
