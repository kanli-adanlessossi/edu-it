"""Unit tests for the privileged-user CLI (task 18.1, Req 13.2, 13.4).

The CLI is driven with an injected in-memory use case, so these tests exercise
the full ``argv -> use case -> exit status`` shell without a database: a valid
invocation assigns the role and exits zero, and every invalid invocation exits
non-zero while making no change.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from edu_it_application.assign_role import AssignRoleUseCase
from edu_it_domain.ports.role_assignment import ROLE_ADMIN, ROLE_REVIEWER
from edu_it_infrastructure.fakes.repositories import (
    In_Memory_RoleAssignmentRepository,
    In_Memory_UserRepository,
)
from edu_it_worker.privileged_user_cli import main


def _ids() -> Iterator[str]:
    index = 0
    while True:
        yield f"user-{index}"
        index += 1


class _Harness:
    def __init__(self) -> None:
        self.users = In_Memory_UserRepository()
        self.roles = In_Memory_RoleAssignmentRepository()
        ids = _ids()
        self.use_case = AssignRoleUseCase(
            users=self.users,
            role_assignments=self.roles,
            stable_id_factory=lambda: next(ids),
        )

    def factory(self, _database_url: str) -> AssignRoleUseCase:
        return self.use_case


def test_valid_admin_invocation_exits_zero_and_assigns() -> None:
    harness = _Harness()
    code = main(
        ["--email", "a@example.test", "--role", ROLE_ADMIN],
        use_case_factory=harness.factory,
    )
    assert code == 0
    created = harness.users.get_by_email("a@example.test")
    assert created is not None
    assert harness.roles.get_for_user(created.stable_id).role == ROLE_ADMIN  # type: ignore[union-attr]


def test_valid_reviewer_invocation_with_track_scope_exits_zero() -> None:
    harness = _Harness()
    code = main(
        ["--email", "r@example.test", "--role", ROLE_REVIEWER, "--track-scope", "python"],
        use_case_factory=harness.factory,
    )
    assert code == 0
    created = harness.users.get_by_email("r@example.test")
    assert created is not None
    assignment = harness.roles.get_for_user(created.stable_id)
    assert assignment is not None
    assert assignment.track_scope == "python"


def test_reviewer_without_track_scope_exits_non_zero_with_no_change() -> None:
    harness = _Harness()
    code = main(
        ["--email", "r@example.test", "--role", ROLE_REVIEWER],
        use_case_factory=harness.factory,
    )
    assert code != 0
    assert len(harness.users) == 0
    assert len(harness.roles) == 0


def test_invalid_email_exits_non_zero_with_no_change() -> None:
    harness = _Harness()
    code = main(
        ["--email", "not-an-email", "--role", ROLE_ADMIN],
        use_case_factory=harness.factory,
    )
    assert code != 0
    assert len(harness.users) == 0
    assert len(harness.roles) == 0


def test_unknown_role_is_rejected_at_parse_time() -> None:
    harness = _Harness()
    # argparse rejects a role outside the grantable choices with SystemExit(2).
    with pytest.raises(SystemExit) as caught:
        main(["--email", "a@example.test", "--role", "wizard"], use_case_factory=harness.factory)
    assert caught.value.code != 0
    assert len(harness.users) == 0


def test_learner_role_is_rejected_at_parse_time() -> None:
    harness = _Harness()
    with pytest.raises(SystemExit) as caught:
        main(["--email", "a@example.test", "--role", "learner"], use_case_factory=harness.factory)
    assert caught.value.code != 0
    assert len(harness.roles) == 0
