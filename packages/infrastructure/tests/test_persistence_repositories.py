"""Unit tests for the PostgreSQL identity repositories (task 7.3, Req 5.1, 5.2, ADR 0004).

The repositories are database-agnostic SQLAlchemy Core over the shared data-model
``Table`` objects, so these tests run them against an in-memory SQLite database
(schema created from the same ``metadata``) with no live PostgreSQL server. SQLite
enforces the ``session -> user`` foreign key once ``PRAGMA foreign_keys`` is on,
so the owner-resolution behaviour is exercised exactly as on PostgreSQL. Coverage:
user insert/lookup with duplicate rejection, durable session create/read, and
idempotent single and bulk revocation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.ports import (
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_infrastructure.persistence import (
    Postgres_SessionRepository,
    Postgres_UserRepository,
    metadata,
)
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    # A single shared in-memory SQLite connection (StaticPool) so every
    # repository operation sees the same database across its short transactions.
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
    return Postgres_UserRepository(engine)


@pytest.fixture
def sessions(engine: Engine) -> Postgres_SessionRepository:
    return Postgres_SessionRepository(engine)


def _user(stable_id: str = "user-1", email: str = "a@example.test") -> UserRecord:
    return UserRecord(
        stable_id=stable_id,
        email=email,
        locale="en",
        status="unverified",
        display_name="A",
    )


# --- UserRepository ----------------------------------------------------------


def test_user_repository_satisfies_the_port(users: Postgres_UserRepository) -> None:
    assert isinstance(users, UserRepository)


def test_add_then_get_by_stable_id_and_email(users: Postgres_UserRepository) -> None:
    users.add(_user())
    by_id = users.get_by_stable_id("user-1")
    by_email = users.get_by_email("a@example.test")
    assert by_id == by_email == _user()


def test_get_missing_user_returns_none(users: Postgres_UserRepository) -> None:
    assert users.get_by_stable_id("missing") is None
    assert users.get_by_email("missing@example.test") is None


def test_duplicate_stable_id_is_rejected(users: Postgres_UserRepository) -> None:
    users.add(_user(stable_id="dup", email="one@example.test"))
    with pytest.raises(ValueError):
        users.add(_user(stable_id="dup", email="two@example.test"))


def test_duplicate_email_is_rejected(users: Postgres_UserRepository) -> None:
    users.add(_user(stable_id="u1", email="same@example.test"))
    with pytest.raises(ValueError):
        users.add(_user(stable_id="u2", email="same@example.test"))


# --- SessionRepository -------------------------------------------------------


def test_session_repository_satisfies_the_port(sessions: Postgres_SessionRepository) -> None:
    assert isinstance(sessions, SessionRepository)


def _make_session(
    users: Postgres_UserRepository,
    sessions: Postgres_SessionRepository,
    ref: str = "ref-1",
    user_stable_id: str = "user-1",
) -> None:
    sessions.add(
        SessionRecord(
            server_side_ref=ref,
            user_stable_id=user_stable_id,
            expires_at=_T0 + timedelta(days=14),
        )
    )


def test_add_then_get_session_resolves_owner_stable_id(
    users: Postgres_UserRepository, sessions: Postgres_SessionRepository
) -> None:
    users.add(_user())
    _make_session(users, sessions)
    record = sessions.get("ref-1")
    assert record is not None
    assert record.user_stable_id == "user-1"
    assert record.is_revoked is False


def test_get_missing_session_returns_none(sessions: Postgres_SessionRepository) -> None:
    assert sessions.get("nope") is None


def test_add_session_for_unknown_owner_is_rejected(
    sessions: Postgres_SessionRepository,
) -> None:
    # No user was inserted, so the owner stable_id resolves to no FK target.
    with pytest.raises(ValueError):
        sessions.add(
            SessionRecord(
                server_side_ref="ref",
                user_stable_id="ghost",
                expires_at=_T0 + timedelta(days=14),
            )
        )


def test_duplicate_server_side_ref_is_rejected(
    users: Postgres_UserRepository, sessions: Postgres_SessionRepository
) -> None:
    users.add(_user())
    _make_session(users, sessions)
    with pytest.raises(ValueError):
        _make_session(users, sessions)


def test_revoke_marks_the_session_revoked_and_is_idempotent(
    users: Postgres_UserRepository, sessions: Postgres_SessionRepository
) -> None:
    users.add(_user())
    _make_session(users, sessions)
    assert sessions.revoke("ref-1", revoked_at=_T0) is True
    revoked = sessions.get("ref-1")
    assert revoked is not None and revoked.is_revoked is True
    # Re-revoking a revoked session, or an unknown one, revokes nothing.
    assert sessions.revoke("ref-1", revoked_at=_T0) is False
    assert sessions.revoke("unknown", revoked_at=_T0) is False


def test_revoke_all_for_user_revokes_only_that_users_live_sessions(
    users: Postgres_UserRepository, sessions: Postgres_SessionRepository
) -> None:
    users.add(_user(stable_id="owner", email="owner@example.test"))
    users.add(_user(stable_id="other", email="other@example.test"))
    _make_session(users, sessions, ref="o1", user_stable_id="owner")
    _make_session(users, sessions, ref="o2", user_stable_id="owner")
    _make_session(users, sessions, ref="x1", user_stable_id="other")

    revoked_count = sessions.revoke_all_for_user("owner", revoked_at=_T0)
    assert revoked_count == 2
    assert sessions.get("x1") is not None and sessions.get("x1").is_revoked is False  # type: ignore[union-attr]
    # Running again revokes nothing new (all of owner's sessions already revoked).
    assert sessions.revoke_all_for_user("owner", revoked_at=_T0) == 0


def test_revoke_all_for_unknown_user_revokes_nothing(
    sessions: Postgres_SessionRepository,
) -> None:
    assert sessions.revoke_all_for_user("ghost", revoked_at=_T0) == 0
