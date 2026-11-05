"""The Migration_Job entry point: ``python -m edu_it_infrastructure.migrate``.

Applies every pending migration to ``head``. Each migration runs in a single
transaction (``transaction_per_migration`` in ``persistence/migrations/env.py``), so on
PostgreSQL, which has transactional DDL, a migration whose statement fails is rolled
back and the schema stays as it was before that migration started (kubernetes-deployment
Req 21.3).

On failure the job logs the failing migration identifier and the database error text,
then exits non-zero so Helm stops the upgrade before it touches the Deployments
(Req 21.1, 21.2). The database URL comes from ``--database-url`` or ``DATABASE_URL``;
neither is ever logged.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable

from edu_it_infrastructure.persistence.runner import Migration_Runner, MigrationFailed

__all__ = ["main"]

_LOG = logging.getLogger("edu_it_infrastructure.migrate")

EXIT_OK = 0
EXIT_MIGRATION_FAILED = 1
EXIT_USAGE = 2


def _database_error_text(cause: BaseException) -> str:
    """Return the database's own error text, without the SQL statement and parameters."""
    original = getattr(cause, "orig", None)
    return str(original if original is not None else cause)


def main(
    argv: list[str] | None = None,
    *,
    runner_factory: Callable[[str], Migration_Runner] = Migration_Runner,
) -> int:
    """Apply all migrations; return the process exit status.

    ``runner_factory`` exists so tests can target an isolated revision history.
    """
    parser = argparse.ArgumentParser(
        prog="python -m edu_it_infrastructure.migrate",
        description="Apply all pending EDU-IT database migrations.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL (defaults to the DATABASE_URL environment variable)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        _LOG.error("no database URL provided (pass --database-url or set DATABASE_URL)")
        return EXIT_USAGE

    runner = runner_factory(database_url)
    try:
        runner.upgrade("head")
    except MigrationFailed as failed:
        _LOG.error(
            "migration %s failed (database left at %s): %s",
            failed.revision if failed.revision is not None else "<unknown>",
            failed.at_version if failed.at_version is not None else "<empty>",
            _database_error_text(failed.cause),
        )
        return EXIT_MIGRATION_FAILED

    _LOG.info("migrations applied; database at %s", runner.current())
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
