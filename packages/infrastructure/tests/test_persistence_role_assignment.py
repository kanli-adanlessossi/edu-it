"""Unit tests for the PostgreSQL role-assignment repository (task 18.1, Req 13).

The adapter is database-agnostic SQLAlchemy Core over the shared ``role_assignment``
and ``user`` tables, so it runs here against in-memory SQLite (schema created from
the same ``metadata``) with no live PostgreSQL. Coverage: grant-and-read, the
reviewer track scope round-trip, last-write-wins replacement, and the unknown-owner
guard.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from edu_it_domain.ports.repository import UserRecord
from edu_it_domain.ports.role_assignment import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    RoleAssignment,
    RoleAssignmentRepository,
)
from edu_it_infrastructure.persistence import (
    Postgres_RoleAssignmentRepository,
    Postgres_UserRepository,
    metadata,
)
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def users(engine: Engine) -> Postgres_UserRepository:
    repo = Postgres_UserRepository(engine)
    repo.add(
        UserRecord(
            stable_id="user-1",
            email="a@example.test",
            locale="en",
            status="verified",
        )
    )
    return repo


@pytest.fixture
def roles(engine: Engine) -> Postgres_RoleAssignmentRepository:
    return Postgres_RoleAssignmentRepository(engine)


def test_satisfies_the_port(roles: Postgres_RoleAssignmentRepository) -> None:
    assert isinstance(roles, RoleAssignmentRepository)


def test_get_for_user_without_assignment_returns_none(
    users: Postgres_UserRepository, roles: Postgres_RoleAssignmentRepository
) -> None:
    assert roles.get_for_user("user-1") is None


def test_set_and_get_admin(
    users: Postgres_UserRepository, roles: Postgres_RoleAssignmentRepository
) -> None:
    roles.set_role(RoleAssignment(user_stable_id="user-1", role=ROLE_ADMIN))
    assignment = roles.get_for_user("user-1")
    assert assignment == RoleAssignment(user_stable_id="user-1", role=ROLE_ADMIN, track_scope=None)


def test_reviewer_track_scope_round_trips(
    users: Postgres_UserRepository, roles: Postgres_RoleAssignmentRepository
) -> None:
    roles.set_role(
        RoleAssignment(user_stable_id="user-1", role=ROLE_REVIEWER, track_scope="python")
    )
    assignment = roles.get_for_user("user-1")
    assert assignment is not None
    assert assignment.role == ROLE_REVIEWER
    assert assignment.track_scope == "python"


def test_set_role_is_last_write_wins(
    users: Postgres_UserRepository, roles: Postgres_RoleAssignmentRepository
) -> None:
    roles.set_role(
        RoleAssignment(user_stable_id="user-1", role=ROLE_REVIEWER, track_scope="python")
    )
    roles.set_role(RoleAssignment(user_stable_id="user-1", role=ROLE_ADMIN))
    assignment = roles.get_for_user("user-1")
    assert assignment is not None
    assert assignment.role == ROLE_ADMIN
    assert assignment.track_scope is None


def test_set_role_for_unknown_owner_is_rejected(
    roles: Postgres_RoleAssignmentRepository,
) -> None:
    with pytest.raises(ValueError):
        roles.set_role(RoleAssignment(user_stable_id="ghost", role=ROLE_ADMIN))
