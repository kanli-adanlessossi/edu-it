"""Create the complete EDU-IT data model.

Revision ID: 0001_create_data_model
Revises:
Create Date: 2024-01-01 00:00:00

This single revision creates exactly one table per entity in the
``docs/DATA-MODEL.md`` (D3) entity groups and ER diagram — identity, catalog,
provenance, progress, mastery & review, AI/review ops, assessments — with no
listed entity omitted and no table beyond those entities (Req 4.1). Each table
carries its internal numeric primary key, its foreign keys, and the unique
constraint on each ``stable_id`` (Req 4.2, 4.4).

Creation uses the authoritative
:data:`~edu_it_infrastructure.persistence.metadata.metadata`, so the tables and
their constraints are created in foreign-key dependency order (parents before
children). ``downgrade`` drops them in the exact reverse order (children before
parents), so applying then rolling back round-trips an empty database to empty
(Req 4.3).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from edu_it_infrastructure.persistence.metadata import metadata

# revision identifiers, used by Alembic.
revision: str = "0001_create_data_model"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create every Data_Model table in foreign-key dependency order."""
    bind = op.get_bind()
    # create_all emits CREATE TABLE in topological order (parents first) and
    # includes each table's primary key, foreign keys, and stable_id unique
    # constraint as declared on the MetaData.
    metadata.create_all(bind=bind, checkfirst=False)


def downgrade() -> None:
    """Drop every Data_Model table in reverse dependency order (Req 4.3)."""
    bind = op.get_bind()
    # drop_all emits DROP TABLE in reverse topological order (children first),
    # leaving no Data_Model table behind.
    metadata.drop_all(bind=bind, checkfirst=False)
