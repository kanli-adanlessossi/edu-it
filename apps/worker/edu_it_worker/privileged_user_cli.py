"""The privileged-user CLI (task 18.1, Req 13.2, 13.4).

This is the thin command-line shell around
:class:`~edu_it_application.assign_role.AssignRoleUseCase`: it parses argv,
resolves the identity ports from the composition root, runs the use case, and
maps the outcome to a process exit status and an operator-facing message. It
lives in ``apps/worker`` — a composition point that may depend on both the
application and infrastructure layers (``worker -> application -> domain <-
infrastructure``) — because wiring a use case to concrete adapters is a
composition concern, not an infrastructure one.

It is the *only* path that provisions the privileged reviewer / author / admin
roles (Req 13.2); the self-service sign-up and GitHub OAuth paths grant only the
learner role (Req 13.3).

Usage::

    edu-it-grant-role --email alice@example.test --role admin
    edu-it-grant-role --email bob@example.test --role reviewer --track-scope python
    edu-it-grant-role ... [--database-url URL]

Exit status (Req 13.2, 13.4):

- ``0`` — a user for the email was created or updated and the requested role was
  assigned (with the track scope for a reviewer); the CLI prints what it did.
- non-zero — the request was invalid (an unknown role, a missing or
  syntactically invalid email, or a reviewer role without a track scope). The
  CLI makes **no** change to any user, reports the invalid input on stderr, and
  exits non-zero. Argparse itself exits ``2`` for missing/unknown flags; a
  validation rejection from the use case exits ``2`` as well.

The use case validates *before* any write, so a rejected request leaves every
user and role assignment untouched (Req 13.4). The CLI never grants the learner
role — it is absent from the grantable-role choices, so passing ``--role
learner`` is rejected at parse time, keeping the self-service/privileged split
intact (Req 13.3).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable

from edu_it_application.assign_role import (
    AssignRoleRequest,
    AssignRoleResult,
    AssignRoleUseCase,
    AssignRoleValidationError,
)
from edu_it_domain.ports.repository import UserRepository
from edu_it_domain.ports.role_assignment import (
    ROLE_ADMIN,
    ROLE_AUTHOR,
    ROLE_REVIEWER,
    RoleAssignmentRepository,
)
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.persistence.repositories import (
    Postgres_RoleAssignmentRepository,
    Postgres_UserRepository,
)

__all__ = ["main"]

#: The roles the CLI accepts on ``--role``. Exactly the privileged roles
#: (Req 13.2, 13.3): reviewer, author, and admin — never learner. Listed
#: explicitly (rather than derived from ``PRIVILEGED_ROLES``) so argparse's
#: ``choices`` renders them in a stable, readable order in ``--help``.
_GRANTABLE_ROLES: tuple[str, ...] = (ROLE_REVIEWER, ROLE_AUTHOR, ROLE_ADMIN)

#: Exit status for an invalid request the use case rejects (Req 13.4). Matches
#: argparse's own exit status for a usage error, so every "bad input" failure of
#: the CLI exits with the same non-zero status.
_INVALID_INPUT_EXIT: int = 2


def _default_use_case_factory(database_url: str) -> AssignRoleUseCase:
    """Build the production :class:`AssignRoleUseCase` from a database URL.

    Wires the PostgreSQL identity repositories (their engine and the psycopg
    driver are created lazily on first use, so building the use case needs no
    live database) and a UUID ``stable_id`` factory for any user the CLI creates.
    Injected into :func:`main` so tests can substitute an in-memory use case.
    """
    users: UserRepository = Postgres_UserRepository(database_url)
    role_assignments: RoleAssignmentRepository = Postgres_RoleAssignmentRepository(database_url)
    return AssignRoleUseCase(
        users=users,
        role_assignments=role_assignments,
        stable_id_factory=lambda: uuid.uuid4().hex,
    )


def main(
    argv: list[str] | None = None,
    *,
    use_case_factory: Callable[[str], AssignRoleUseCase] = _default_use_case_factory,
) -> int:
    """CLI entry point for the privileged-user command (Req 13.2, 13.4).

    Parses ``argv``, builds the use case from the resolved database URL, and runs
    it. Returns ``0`` on a successful create-or-update-and-assign and a non-zero
    status on an invalid request, printing an operator-facing message. When
    ``--database-url`` is omitted the URL is read from ``DATABASE_URL`` (falling
    back to the composition default), so the CLI needs no flag in a configured
    environment.

    ``use_case_factory`` builds the :class:`AssignRoleUseCase` from the resolved
    database URL; it defaults to the production wiring and exists only so tests
    can inject an in-memory use case. The public CLI surface is unchanged.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    database_url = args.database_url or CompositionSettings.from_env().database_url
    use_case = use_case_factory(database_url)

    request = AssignRoleRequest(
        email=args.email,
        role=args.role,
        track_scope=args.track_scope,
    )
    try:
        result = use_case.execute(request)
    except AssignRoleValidationError as rejected:
        print(
            f"error: cannot assign role: {_describe_reason(rejected.reason)}",
            file=sys.stderr,
        )
        return _INVALID_INPUT_EXIT

    _report_success(result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the privileged-user CLI.

    ``--role`` is restricted to the grantable privileged roles via ``choices`` so
    an unknown role is rejected at parse time (exit ``2``); ``--email`` and
    ``--role`` are required, and ``--track-scope`` is optional at the parser level
    (the reviewer requirement is enforced by the use case so the "no change on
    invalid input" guarantee holds for it too, Req 13.4).
    """
    parser = argparse.ArgumentParser(
        prog="edu-it-grant-role",
        description="Create or update a user and grant a privileged role (Req 13).",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="the target user's email address (created if no user exists)",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=_GRANTABLE_ROLES,
        help="the privileged role to grant (reviewer, author, or admin)",
    )
    parser.add_argument(
        "--track-scope",
        default=None,
        help="the reviewer's track scope (required for --role reviewer)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="target SQLAlchemy database URL (defaults to DATABASE_URL in the environment)",
    )
    return parser


def _describe_reason(reason: str) -> str:
    """Render a use-case rejection reason as an operator-facing message (Req 13.4)."""
    messages = {
        "unknown_role": "the role is not a grantable privileged role",
        "invalid_email": "the target email is missing or syntactically invalid",
        "reviewer_requires_track_scope": "a reviewer role requires a track scope (--track-scope)",
    }
    return messages.get(reason, reason)


def _report_success(result: AssignRoleResult) -> None:
    """Print what the CLI did on a successful assignment (Req 13.2)."""
    action = "created user and assigned" if result.created_user else "assigned"
    assignment = result.assignment
    scope = f" (track scope: {assignment.track_scope})" if assignment.track_scope else ""
    print(
        f"{action} role '{assignment.role}' to user {assignment.user_stable_id}{scope}",
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
