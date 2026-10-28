"""The Migration_Runner: apply, roll back, and idempotently re-apply (Req 4).

:class:`Migration_Runner` wraps the Alembic command API and drives the revision
scripts in ``migrations/versions`` against a target database URL. It provides:

- :meth:`Migration_Runner.upgrade` — apply all migrations to ``head``. Against
  an empty database this creates the complete schema (Req 4.2); against a
  database already at ``head`` it makes no schema change (idempotent, Req 4.6).
- :meth:`Migration_Runner.downgrade` — roll back to ``base``, removing the
  Data_Model tables in reverse dependency order (Req 4.3).
- :meth:`Migration_Runner.current` — the current migration version of a
  database, used to confirm the last committed version after a failure.

Each migration runs in its own transaction (``transaction_per_migration`` in
``env.py``), so a migration that fails partway is rolled back and the database
is left at the last successfully committed version. The runner surfaces the
failure as a :class:`MigrationFailed` carrying the failing revision, and the
CLI (:func:`main`) maps that to a non-zero exit with a failing-migration report
(Req 4.5).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

__all__ = ["MigrationFailed", "Migration_Runner", "main"]

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_ALEMBIC_INI = _MIGRATIONS_DIR / "alembic.ini"


class MigrationFailed(RuntimeError):
    """A migration failed partway through application (Req 4.5).

    Carries the ``revision`` that failed and the ``at_version`` the database was
    left at (the last successfully committed version) so callers can report the
    failing migration without re-inspecting Alembic internals.
    """

    def __init__(self, revision: str | None, at_version: str | None, cause: BaseException):
        self.revision = revision
        self.at_version = at_version
        self.cause = cause
        detail = revision if revision is not None else "<unknown>"
        super().__init__(f"migration {detail!s} failed: {cause}")


class Migration_Runner:
    """Drives the EDU-IT Alembic migrations against a target database URL."""

    def __init__(
        self,
        database_url: str,
        *,
        version_locations: str | Path | None = None,
    ) -> None:
        """Create a runner targeting ``database_url`` (a SQLAlchemy URL).

        ``version_locations`` overrides the directory Alembic scans for revision
        scripts. It defaults to this package's ``migrations/versions`` directory
        (the production schema). Tests pass an isolated directory so they can
        exercise alternate revision histories (for example, a deliberately
        failing revision) without mutating the shared package directory, which
        would otherwise change the ambient ``head`` seen by concurrent runners.
        """
        self._database_url = database_url
        self._version_locations = (
            Path(version_locations)
            if version_locations is not None
            else _MIGRATIONS_DIR / "versions"
        )
        self._config = self._build_config(database_url, self._version_locations)
        self._script = ScriptDirectory.from_config(self._config)

    @staticmethod
    def _build_config(database_url: str, version_locations: Path) -> Config:
        """Build an Alembic ``Config`` pointing at the migration scripts."""
        config = Config(str(_ALEMBIC_INI))
        # Resolve script and version locations relative to this package so the
        # runner works regardless of the current working directory. The version
        # location is overridable so tests can target an isolated revision set.
        config.set_main_option("script_location", str(_MIGRATIONS_DIR))
        config.set_main_option("version_locations", str(version_locations))
        config.set_main_option("sqlalchemy.url", database_url)
        return config

    def current(self) -> str | None:
        """Return the database's current migration version (``None`` if empty)."""
        engine = create_engine(self._database_url)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(connection)
                return context.get_current_revision()
        finally:
            engine.dispose()

    def head(self) -> str | None:
        """Return the head revision defined by the migration scripts."""
        return self._script.get_current_head()

    def upgrade(self, target: str = "head") -> None:
        """Apply migrations up to ``target`` (default ``head``).

        Idempotent at ``head``: when the database is already at the target this
        applies no step and makes no schema change (Req 4.6). A migration that
        fails partway leaves the database at the last committed version and
        raises :class:`MigrationFailed` naming the failing revision (Req 4.5).
        """
        from alembic import command

        try:
            command.upgrade(self._config, target)
        except Exception as exc:
            raise self._as_migration_failed(exc) from exc

    def downgrade(self, target: str = "base") -> None:
        """Roll back migrations down to ``target`` (default ``base``).

        At ``base`` this removes the Data_Model tables in reverse dependency
        order until none remains (Req 4.3). Idempotent when already at the
        target.
        """
        from alembic import command

        try:
            command.downgrade(self._config, target)
        except Exception as exc:
            raise self._as_migration_failed(exc) from exc

    def _as_migration_failed(self, exc: BaseException) -> MigrationFailed:
        """Wrap a raw migration error with the failing/last-committed revisions.

        After a per-migration transaction rolls back, the database's current
        version is the last successfully committed one; the failing revision is
        the next step Alembic attempted from there.
        """
        at_version = self.current()
        failing = self._next_revision(at_version)
        return MigrationFailed(failing, at_version, exc)

    def _next_revision(self, from_version: str | None) -> str | None:
        """The revision Alembic would apply next after ``from_version``."""
        head = self._script.get_current_head()
        if head is None or head == from_version:
            return None
        # Walk from head back toward from_version; the step whose down_revision
        # is from_version is the one that would have been applied next.
        for revision in self._script.walk_revisions(base="base", head="heads"):
            down = revision.down_revision
            down_key = down if isinstance(down, str) or down is None else next(iter(down), None)
            if down_key == from_version:
                return revision.revision
        return head


def main(
    argv: list[str] | None = None,
    *,
    runner_factory: Callable[[str], Migration_Runner] = Migration_Runner,
) -> int:
    """CLI entry point for the Migration_Runner (Req 4).

    Usage::

        edu-it-migrate upgrade [--database-url URL] [--target head]
        edu-it-migrate downgrade [--database-url URL] [--target base]
        edu-it-migrate current [--database-url URL]

    Exits ``0`` on success. On a mid-migration failure it reports the failing
    migration on stderr and exits non-zero (Req 4.5). When ``--database-url`` is
    omitted the runner reads ``DATABASE_URL`` from the environment.

    ``runner_factory`` builds the :class:`Migration_Runner` from the resolved
    database URL. It defaults to the class itself and exists only so tests can
    inject a runner pointed at an isolated revision history; the public CLI
    surface is unchanged.
    """
    import os

    parser = argparse.ArgumentParser(
        prog="edu-it-migrate",
        description="EDU-IT data-model migrations",
    )
    parser.add_argument(
        "action",
        choices=("upgrade", "downgrade", "current"),
        help="apply migrations (upgrade), roll them back (downgrade), or show the current version",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="target SQLAlchemy database URL (defaults to the DATABASE_URL environment variable)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="target revision (defaults to 'head' for upgrade, 'base' for downgrade)",
    )
    args = parser.parse_args(argv)

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "error: no database URL provided (pass --database-url or set DATABASE_URL)",
            file=sys.stderr,
        )
        return 2

    runner = runner_factory(database_url)

    if args.action == "current":
        print(runner.current() or "<empty>")
        return 0

    try:
        if args.action == "upgrade":
            runner.upgrade(args.target or "head")
        else:
            runner.downgrade(args.target or "base")
    except MigrationFailed as failed:
        print(
            f"error: migration failed at revision {failed.revision!s}; "
            f"database left at last committed version {failed.at_version!s}: {failed.cause}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
