"""Alembic environment: runs migrations online against a live connection.

The :class:`~edu_it_infrastructure.persistence.runner.Migration_Runner` injects
the target database URL into the Alembic ``Config`` before invoking the Alembic
command API, so this environment reads the URL from the config and binds the
declared :data:`~edu_it_infrastructure.persistence.metadata.metadata` as the
autogenerate/compare target.

This phase only runs explicit revision scripts (no autogenerate at runtime); the
``target_metadata`` wiring keeps the environment complete and lets future
autogenerate diff against the authoritative schema.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from edu_it_infrastructure.persistence.metadata import metadata

# The Alembic Config object, providing access to values in alembic.ini and any
# values injected by the Migration_Runner (notably sqlalchemy.url).
config = context.config

# The schema the migrations build toward (Req 4.1).
target_metadata = metadata


def run_migrations_offline() -> None:
    """Emit migrations as SQL without a DBAPI connection ("offline" mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection in a transaction.

    Each migration runs inside a transaction so that a migration which fails
    partway is rolled back and the database is left at the last successfully
    committed version (Req 4.5).
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
