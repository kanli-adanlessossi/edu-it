"""The privileged-role assignment use case (task 18.1, Req 13.2, 13.4).

:class:`AssignRoleUseCase` is the framework-free core behind the privileged-user
CLI (Req 13.2). It assigns one *privileged* role — reviewer or admin (and, by the
role vocabulary, author) — to the user with a given email, creating the user when
none exists and updating the existing user otherwise, and records the role
assignment (with the track scope for a reviewer). The CLI at the infrastructure
edge parses argv, resolves the ports, and maps this use case's result to an exit
status and message (Req 13.2, 13.4); the behavior itself lives here, with no web,
API, or CLI framework imported (Req 1.8).

Validation is total and happens *before* any state changes, so an invalid request
changes nothing (Req 13.4):

- The target email must be syntactically valid (the same minimal well-formed
  shape the sign-up core accepts, no longer than 254 characters).
- The role must be one the CLI may grant — a *privileged* role (reviewer, author,
  or admin). An unknown role, or the learner role (which is self-service only,
  never CLI-provisioned, Req 13.3), is rejected.
- A reviewer role must carry a non-empty track scope; every non-reviewer role
  must carry none (Req 13.1).

On a valid request the use case ensures a user exists for the email (creating a
verified user with the learner base status when absent, or reusing the existing
one) and then records the role assignment, which is idempotent/last-write-wins at
the store boundary. Because validation is complete before the first write, a
rejected request never creates a user or touches a role assignment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from edu_it_domain.ports.repository import (
    USER_STATUS_VERIFIED,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.role_assignment import (
    PRIVILEGED_ROLES,
    ROLE_REVIEWER,
    RoleAssignment,
    RoleAssignmentRepository,
)

__all__ = [
    "MAX_EMAIL_LENGTH",
    "AssignRoleRequest",
    "AssignRoleResult",
    "AssignRoleUseCase",
    "AssignRoleValidationError",
]

#: The maximum accepted email length (matches the sign-up core, Req 13.2): a
#: well-formed email no longer than 254 characters (the practical maximum).
MAX_EMAIL_LENGTH: Final[int] = 254


@dataclass(frozen=True, slots=True)
class AssignRoleRequest:
    """The privileged-role assignment the CLI asks the use case to perform.

    ``email`` is the target user's address, ``role`` the privileged role to
    grant, and ``track_scope`` the reviewer's per-track expertise — required for
    the reviewer role and absent (``None``) for every other role (Req 13.1).
    """

    email: str
    """The target user's email address."""

    role: str
    """The privileged role to grant (reviewer, author, or admin)."""

    track_scope: str | None = None
    """The reviewer's track scope; required for reviewer, ``None`` otherwise."""


@dataclass(frozen=True, slots=True)
class AssignRoleResult:
    """The outcome of a successful role assignment (Req 13.2).

    Carries the resulting :class:`RoleAssignment` and whether a new user was
    created (``True``) or an existing one updated (``False``), so the CLI can
    report what it did and exit zero.
    """

    assignment: RoleAssignment
    """The role assignment now recorded for the target user."""

    created_user: bool
    """Whether a new user was created (vs. an existing user updated)."""


class AssignRoleValidationError(Exception):
    """Raised when the assignment request is invalid (Req 13.4).

    Raised *before* any state change, so a rejected request creates no user and
    records no role assignment. :attr:`reason` is a short, stable machine code
    (``unknown_role``, ``invalid_email``, or ``reviewer_requires_track_scope``)
    identifying the invalid input, which the CLI reports on stderr while exiting
    non-zero.
    """

    def __init__(self, reason: str) -> None:
        self.reason: str = reason
        super().__init__(f"role assignment rejected: {reason}")


class AssignRoleUseCase:
    """Assign a privileged role to a user by email (Req 13.2, 13.4)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        role_assignments: RoleAssignmentRepository,
        stable_id_factory: Callable[[], str],
    ) -> None:
        """Wire the use case to its collaborators.

        ``stable_id_factory`` mints the ``stable_id`` for a newly created target
        user; it is injected so the flow is deterministic under test.
        """
        self._users = users
        self._role_assignments = role_assignments
        self._stable_id_factory = stable_id_factory

    def execute(self, request: AssignRoleRequest) -> AssignRoleResult:
        """Validate, ensure the user exists, and record the role assignment.

        Validates the request completely first and raises
        :class:`AssignRoleValidationError` on an unknown/ineligible role, an
        invalid email, or a reviewer role without a track scope, before touching
        any state (Req 13.4). For a valid request, creates the target user when
        absent (verified, learner base status) or reuses the existing one, then
        records the role assignment with the track scope for a reviewer and
        returns the result (Req 13.2).
        """
        self._validate(request)

        existing = self._users.get_by_email(request.email)
        if existing is None:
            user = UserRecord(
                stable_id=self._stable_id_factory(),
                email=request.email,
                locale="en",
                status=USER_STATUS_VERIFIED,
            )
            self._users.add(user)
            created_user = True
        else:
            user = existing
            created_user = False

        assignment = RoleAssignment(
            user_stable_id=user.stable_id,
            role=request.role,
            track_scope=request.track_scope if request.role == ROLE_REVIEWER else None,
        )
        self._role_assignments.set_role(assignment)
        return AssignRoleResult(assignment=assignment, created_user=created_user)

    def _validate(self, request: AssignRoleRequest) -> None:
        """Reject an invalid request before any state change (Req 13.4).

        The role is checked first, then the email, then the reviewer track-scope
        rule, raising on the first problem found with a stable reason code.
        """
        if request.role not in PRIVILEGED_ROLES:
            raise AssignRoleValidationError("unknown_role")
        if not _is_valid_email(request.email):
            raise AssignRoleValidationError("invalid_email")
        if request.role == ROLE_REVIEWER and not _has_track_scope(request.track_scope):
            raise AssignRoleValidationError("reviewer_requires_track_scope")


def _is_valid_email(email: str) -> bool:
    """Return whether ``email`` is syntactically valid and within length.

    Uses the same minimal well-formed shape the sign-up core accepts (exactly one
    ``@`` separating a non-empty local part from a dotted domain, no whitespace),
    bounded by :data:`MAX_EMAIL_LENGTH`, so the CLI and self-service paths agree
    on what a valid email is.
    """
    if not email or len(email) > MAX_EMAIL_LENGTH:
        return False
    if any(character.isspace() for character in email):
        return False
    local, separator, domain = email.partition("@")
    if separator != "@" or "@" in domain:
        return False
    if not local or not domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    return all(labels)


def _has_track_scope(track_scope: str | None) -> bool:
    """Return whether ``track_scope`` is a present, non-blank value (Req 13.1)."""
    return track_scope is not None and track_scope.strip() != ""
