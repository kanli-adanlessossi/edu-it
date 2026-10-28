"""Unit tests for the privileged-role assignment use case (task 18.1, Req 13.2, 13.4).

These exercise :class:`~edu_it_application.assign_role.AssignRoleUseCase` against
the in-memory identity fakes: a valid request creates-or-updates a user and
records exactly the requested role (with the track scope for a reviewer) and the
invalid requests — an unknown/ineligible role, a bad email, or a reviewer without
a track scope — are rejected before any state change (Req 13.4).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from edu_it_application.assign_role import (
    AssignRoleRequest,
    AssignRoleUseCase,
    AssignRoleValidationError,
)
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    UserRecord,
)
from edu_it_domain.ports.role_assignment import (
    ROLE_ADMIN,
    ROLE_LEARNER,
    ROLE_REVIEWER,
)
from edu_it_infrastructure.fakes.repositories import (
    In_Memory_RoleAssignmentRepository,
    In_Memory_UserRepository,
)


def _ids() -> Iterator[str]:
    index = 0
    while True:
        yield f"user-{index}"
        index += 1


def _build() -> tuple[
    AssignRoleUseCase, In_Memory_UserRepository, In_Memory_RoleAssignmentRepository
]:
    users = In_Memory_UserRepository()
    roles = In_Memory_RoleAssignmentRepository()
    ids = _ids()
    use_case = AssignRoleUseCase(
        users=users,
        role_assignments=roles,
        stable_id_factory=lambda: next(ids),
    )
    return use_case, users, roles


def test_assigns_admin_to_a_new_user_creating_it() -> None:
    use_case, users, roles = _build()

    result = use_case.execute(AssignRoleRequest(email="a@example.test", role=ROLE_ADMIN))

    assert result.created_user is True
    assert len(users) == 1
    created = users.get_by_email("a@example.test")
    assert created is not None
    assignment = roles.get_for_user(created.stable_id)
    assert assignment is not None
    assert assignment.role == ROLE_ADMIN
    assert assignment.track_scope is None


def test_assigns_reviewer_with_track_scope() -> None:
    use_case, _users, roles = _build()

    result = use_case.execute(
        AssignRoleRequest(email="r@example.test", role=ROLE_REVIEWER, track_scope="python")
    )

    assert result.assignment.role == ROLE_REVIEWER
    assert result.assignment.track_scope == "python"
    assert roles.get_for_user(result.assignment.user_stable_id) == result.assignment


def test_updates_an_existing_user_without_creating_a_duplicate() -> None:
    use_case, users, roles = _build()
    users.add(
        UserRecord(
            stable_id="existing",
            email="e@example.test",
            locale="fr",
            status=USER_STATUS_UNVERIFIED,
        )
    )

    result = use_case.execute(AssignRoleRequest(email="e@example.test", role=ROLE_ADMIN))

    assert result.created_user is False
    assert len(users) == 1
    assert roles.get_for_user("existing") is not None
    assert roles.get_for_user("existing").role == ROLE_ADMIN  # type: ignore[union-attr]


def test_reassigning_is_last_write_wins() -> None:
    use_case, _users, roles = _build()
    use_case.execute(
        AssignRoleRequest(email="x@example.test", role=ROLE_REVIEWER, track_scope="python")
    )
    result = use_case.execute(AssignRoleRequest(email="x@example.test", role=ROLE_ADMIN))

    assignment = roles.get_for_user(result.assignment.user_stable_id)
    assert assignment is not None
    assert assignment.role == ROLE_ADMIN
    assert assignment.track_scope is None
    assert len(roles) == 1


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (AssignRoleRequest(email="a@example.test", role="wizard"), "unknown_role"),
        (AssignRoleRequest(email="a@example.test", role=ROLE_LEARNER), "unknown_role"),
        (AssignRoleRequest(email="not-an-email", role=ROLE_ADMIN), "invalid_email"),
        (AssignRoleRequest(email="", role=ROLE_ADMIN), "invalid_email"),
        (
            AssignRoleRequest(email="r@example.test", role=ROLE_REVIEWER),
            "reviewer_requires_track_scope",
        ),
        (
            AssignRoleRequest(email="r@example.test", role=ROLE_REVIEWER, track_scope="  "),
            "reviewer_requires_track_scope",
        ),
    ],
)
def test_invalid_requests_are_rejected_with_no_state_change(
    request_: AssignRoleRequest, reason: str
) -> None:
    use_case, users, roles = _build()

    with pytest.raises(AssignRoleValidationError) as caught:
        use_case.execute(request_)

    assert caught.value.reason == reason
    # No user created and no role assignment recorded (Req 13.4).
    assert len(users) == 0
    assert len(roles) == 0


def test_learner_role_is_never_cli_grantable() -> None:
    use_case, _users, _roles = _build()
    with pytest.raises(AssignRoleValidationError):
        use_case.execute(AssignRoleRequest(email="a@example.test", role=ROLE_LEARNER))
