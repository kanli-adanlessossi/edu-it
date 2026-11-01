"""Account runtime tables: ``credential`` and ``verification_link``.

Revision ID: 0003_accounts_runtime
Revises: 0002_labs_runtime
Create Date: 2024-01-03 00:00:00

Creates exactly two tables (api-route-wiring design, "Revision 0003"; Req 7.4,
7.9): ``credential`` (unique ``user_id``) and ``verification_link`` (unique
``token_digest``). No other table is created. ``0001`` skips these two tables,
so ``downgrade`` drops only them and leaves every earlier table intact.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from edu_it_infrastructure.persistence.metadata import metadata

# revision identifiers, used by Alembic.
revision: str = "0003_accounts_runtime"
down_revision: str | None = "0002_labs_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Creation order; ``verification_link`` and ``credential`` only reference ``user``.
_TABLE_NAMES = ("credential", "verification_link")


def upgrade() -> None:
    """Create ``credential`` and ``verification_link`` with their constraints."""
    bind = op.get_bind()
    tables = [metadata.tables[name] for name in _TABLE_NAMES]
    metadata.create_all(bind=bind, tables=tables, checkfirst=False)


def downgrade() -> None:
    """Drop only ``verification_link`` and ``credential``."""
    bind = op.get_bind()
    tables = [metadata.tables[name] for name in reversed(_TABLE_NAMES)]
    metadata.drop_all(bind=bind, tables=tables, checkfirst=False)
