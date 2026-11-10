"""Seed a local development stack: a dev admin and the approval of the synced content.

Usage (the start scripts run it after ``tools/e2e_stack.py up``)::

    python tools/dev_seed.py [--dry-run]

What it does, in order, against the migrated database of the local stack:

1. creates (or reuses) a *verified* user for ``DEV_ADMIN_EMAIL`` and grants it the ``admin``
   role, through the same ``AssignRoleUseCase`` the ``edu-it-grant-role`` command uses;
2. stores ``DEV_ADMIN_PASSWORD`` for that user (scrypt hash, ``Postgres_CredentialStore``);
3. re-runs ``edu-it-content sync --approved-by <that user's stable id>``, which stamps the
   reviewer on the synced content and marks it as the Golden_Set. The sync is idempotent and
   a later approval is not a content change, so the items ``up`` already synced are only
   attributed, never duplicated.

Every step is idempotent: a second run reuses the user, re-stores the same password and
re-applies the same approval.

The credentials are *not* in the repository. They come from the environment variables
``DEV_ADMIN_EMAIL`` and ``DEV_ADMIN_PASSWORD``, or from the git-ignored ``.env.dev-admin`` file
at the repository root (``KEY=value`` lines, read literally: no quoting, no shell expansion).
Without both values the script prints how to provide them and exits ``0`` without seeding, so
the start scripts keep working on a machine that has no dev admin configured.

This is a *development* convenience. The password is stored without the 12-character sign-up
policy check (sign-in does not apply it; sign-up and password reset do), and it must never be
used against a shared or production database: the script refuses a database URL whose host is
not local.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse

from e2e_stack import DATABASE_URL, REPO_ROOT

CREDENTIALS_FILE = REPO_ROOT / ".env.dev-admin"
EMAIL_VAR = "DEV_ADMIN_EMAIL"
PASSWORD_VAR = "DEV_ADMIN_PASSWORD"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines literally (a ``$`` or ``#`` inside a value is kept)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def resolve_credentials(
    env: Mapping[str, str], file_values: Mapping[str, str]
) -> tuple[str, str] | None:
    """The (email, password) pair: the environment wins over the file. ``None`` if incomplete."""
    email = env.get(EMAIL_VAR) or file_values.get(EMAIL_VAR) or ""
    password = env.get(PASSWORD_VAR) or file_values.get(PASSWORD_VAR) or ""
    if not email or not password:
        return None
    return email, password


def ensure_local_database(url: str) -> None:
    """Refuse to seed a dev admin into a database that is not on this machine."""
    host = urlparse(url).hostname or ""
    if host not in _LOCAL_HOSTS:
        raise SystemExit(f"dev_seed: refusing to seed {host!r}; only a local database is allowed")


def seed_admin(database_url: str, email: str, password: str) -> str:
    """Create or reuse the verified admin, store its password, return its ``stable_id``."""
    from edu_it_application.assign_role import AssignRoleRequest, AssignRoleUseCase
    from edu_it_domain.ports.role_assignment import ROLE_ADMIN
    from edu_it_infrastructure.password_hasher import Scrypt_PasswordHasher
    from edu_it_infrastructure.persistence.account_repositories import Postgres_CredentialStore
    from edu_it_infrastructure.persistence.repositories import (
        Postgres_RoleAssignmentRepository,
        Postgres_UserRepository,
    )

    users = Postgres_UserRepository(database_url)
    roles = Postgres_RoleAssignmentRepository(database_url)
    hasher = Scrypt_PasswordHasher()
    credentials = Postgres_CredentialStore(database_url, hasher)

    result = AssignRoleUseCase(
        users=users, role_assignments=roles, stable_id_factory=lambda: uuid.uuid4().hex
    ).execute(AssignRoleRequest(email=email, role=ROLE_ADMIN))
    stable_id = result.assignment.user_stable_id
    credentials.set_password(stable_id, hasher.hash(password))
    action = "created" if result.created_user else "reused"
    print(f"dev_seed: {action} admin user {email} ({stable_id}); password stored")
    return stable_id


def approve_content(database_url: str, reviewer_stable_id: str, env: Mapping[str, str]) -> int:
    """Re-run the idempotent content sync with the admin as the approving reviewer."""
    from edu_it_content.cli import main as content_main

    content_root = env.get("EDU_IT_CONTENT_ROOT") or str(REPO_ROOT / "content")
    return content_main(
        [
            "sync",
            "--content-root",
            content_root,
            "--database-url",
            database_url,
            "--approved-by",
            reviewer_stable_id,
        ]
    )


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev_seed", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    database_url = env.get("DATABASE_URL") or DATABASE_URL
    credentials = resolve_credentials(env, read_env_file(CREDENTIALS_FILE))
    if credentials is None:
        print(
            f"dev_seed: no dev admin configured; skipping. Set {EMAIL_VAR} and {PASSWORD_VAR}, "
            f"or put them in {CREDENTIALS_FILE.name} (git-ignored)."
        )
        return 0
    email, _ = credentials
    ensure_local_database(database_url)
    if args.dry_run:
        print(f"1. create or reuse verified admin {email} and store its password")
        print("2. content sync --approved-by <that user's stable id>")
        return 0

    stable_id = seed_admin(database_url, *credentials)
    status = approve_content(database_url, stable_id, env)
    if status != 0:
        print(f"dev_seed: content approval failed (exit {status})", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
