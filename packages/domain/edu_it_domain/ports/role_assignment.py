"""The identity role model and its repository port (Req 13.1, 13.2, 13.4).

The identity model supports exactly four roles — learner, reviewer, author, and
admin (Req 13.1), per #[[file:.kiro/steering/auth-privacy.md]]. A role is granted
to a user through a durable :class:`RoleAssignment`, the ``role_assignment``
entity of the data model's identity group (D3 Section 4): a ``role`` plus, for
the reviewer role only, a ``track_scope`` recording the reviewer's per-track
expertise. Every other role carries no track scope.

Like the sibling identity ports (ADR 0004), this is a framework-free
:class:`typing.Protocol`: the application layer depends on the protocol, the
infrastructure layer supplies a PostgreSQL implementation, and tests use an
in-memory one. The owning user is referenced by its ``stable_id`` so the domain
never sees an internal numeric key.

Role separation (Req 13.3). The privileged roles — reviewer, author, and admin —
are provisioned only through the privileged-user CLI (Req 13.2); the self-service
sign-up and GitHub OAuth paths assign only :data:`ROLE_LEARNER`. This module
holds the role vocabulary and the assignment record/port; the CLI and the
assign-role use case that enforce the self-service/privileged split live in the
application and infrastructure layers that build on this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "PRIVILEGED_ROLES",
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_AUTHOR",
    "ROLE_LEARNER",
    "ROLE_REVIEWER",
    "RoleAssignment",
    "RoleAssignmentRepository",
]

#: The base role every self-service account carries (Req 7.9, 13.3). Assigned by
#: sign-up and GitHub OAuth; never granted by the privileged-user CLI, which
#: provisions only the privileged roles below.
ROLE_LEARNER: Final[str] = "learner"

#: The reviewer role (Req 13.1). Unique among the roles in carrying a
#: ``track_scope`` — the reviewer's per-track expertise recorded as the role
#: assignment's track scope (Req 13.1) — which is therefore mandatory for this
#: role and absent for every other.
ROLE_REVIEWER: Final[str] = "reviewer"

#: The author role (Req 13.1): a privileged, CLI-provisioned role with no track
#: scope.
ROLE_AUTHOR: Final[str] = "author"

#: The admin role (Req 13.1): a privileged, CLI-provisioned role with no track
#: scope.
ROLE_ADMIN: Final[str] = "admin"

#: The complete, closed set of roles the identity model supports (Req 13.1).
#: Exactly learner, reviewer, author, and admin — no more and no fewer.
ROLES: Final[frozenset[str]] = frozenset({ROLE_LEARNER, ROLE_REVIEWER, ROLE_AUTHOR, ROLE_ADMIN})

#: The roles that may be granted only through the privileged-user CLI (Req 13.2,
#: 13.3): reviewer, author, and admin. The learner role is deliberately absent —
#: it is the self-service role and is never provisioned by the CLI.
PRIVILEGED_ROLES: Final[frozenset[str]] = frozenset({ROLE_REVIEWER, ROLE_AUTHOR, ROLE_ADMIN})


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    """A durable grant of one role to a user (Req 13.1, 13.2).

    The ``role_assignment`` entity of the identity group (D3 Section 4): the
    owning user (by :attr:`user_stable_id`), the granted :attr:`role` (one of
    :data:`ROLES`), and, for the reviewer role only, the :attr:`track_scope`
    recording that reviewer's per-track expertise. :attr:`track_scope` is the
    stable id of the track the reviewer is scoped to; it is ``None`` for every
    non-reviewer role and required for the reviewer role (Req 13.1).
    """

    user_stable_id: str
    """The ``stable_id`` of the user the role is granted to."""

    role: str
    """The granted role (one of :data:`ROLES`)."""

    track_scope: str | None = None
    """The reviewer's track scope (reviewer only); ``None`` for every other role."""


@runtime_checkable
class RoleAssignmentRepository(Protocol):
    """Port for reading and writing durable ``role_assignment`` records (Req 13)."""

    def set_role(self, assignment: RoleAssignment) -> None:
        """Grant ``assignment``'s role to its user, replacing any prior grant.

        Assigning a role to a user is idempotent and last-write-wins: a user
        carries the single role most recently assigned by the CLI, so re-running
        the CLI for the same user updates rather than accumulates (Req 13.2).
        """
        ...

    def get_for_user(self, user_stable_id: str) -> RoleAssignment | None:
        """Return the role assignment for ``user_stable_id``, or ``None``.

        ``None`` when the user has no explicit assignment recorded (a brand-new
        self-service user carries the learner role implicitly until one is set).
        """
        ...
