"""The PostgreSQL identity repositories (task 7.3, Req 5.1, 5.2, ADR 0004).

:class:`Postgres_UserRepository` and :class:`Postgres_SessionRepository` implement
the domain :class:`~edu_it_domain.ports.repository.UserRepository` and
:class:`~edu_it_domain.ports.repository.SessionRepository` ports over PostgreSQL
using SQLAlchemy Core against the ``user`` and ``session`` tables defined in
:mod:`~edu_it_infrastructure.persistence.metadata` (the full schema migrated by
the Migration_Runner, Req 4). They do **not** redefine the schema — they read and
write the existing Core ``Table`` objects — and they never surface a row's
internal numeric primary key: a user is addressed by its ``stable_id`` and a
session by its opaque ``server_side_ref`` (D3 Section 1, D2 Section 4).

Per ADR 0004 the durable ``session`` record in PostgreSQL is the identity system
of record; the fast ephemeral session state lives in Redis behind the separate
``SessionStore`` port. These repositories own the durable records, so session
ownership stays re-verifiable server-side and GDPR export/deletion continue to
cover sessions.

Each adapter holds a SQLAlchemy :class:`~sqlalchemy.engine.Engine` and opens a
short transaction (``engine.begin``) per operation, so a write is committed
atomically and a read sees a consistent snapshot. The adapters are database-agnostic
SQLAlchemy Core, so the same code runs against PostgreSQL in production and against
SQLite in unit tests (the ``session`` foreign key to ``user`` is honoured by both).
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    USER_STATUS_VERIFIED,
    SessionRecord,
    SessionRepository,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.role_assignment import (
    RoleAssignment,
    RoleAssignmentRepository,
)
from sqlalchemy import and_, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.metadata import role_assignment as role_assignment_table
from edu_it_infrastructure.persistence.metadata import session as session_table
from edu_it_infrastructure.persistence.metadata import user as user_table

__all__ = [
    "Postgres_RoleAssignmentRepository",
    "Postgres_SessionRepository",
    "Postgres_UserRepository",
]


class _EngineHandle:
    """Holds a SQLAlchemy engine, creating it lazily from a URL on first use.

    A repository may be given a ready :class:`~sqlalchemy.engine.Engine` (tests
    pass a SQLite one) or a database URL (the composition root passes the
    production ``DATABASE_URL``). When given a URL the engine — and therefore the
    DBAPI driver import (psycopg) — is deferred until the first database
    operation, so assembling the ports never requires the driver's native libpq
    to be present, matching the lazy wiring the composition root documents.
    """

    def __init__(self, engine_or_url: Engine | str) -> None:
        self._url: str | None = None
        self._engine: Engine | None = None
        if isinstance(engine_or_url, str):
            self._url = engine_or_url
        else:
            self._engine = engine_or_url

    @property
    def engine(self) -> Engine:
        """Return the engine, creating it from the URL on first access."""
        if self._engine is None:
            assert self._url is not None  # one of url/engine is always set
            self._engine = create_engine(self._url)
        return self._engine


class Postgres_UserRepository(UserRepository):
    """A :class:`UserRepository` backed by the PostgreSQL ``user`` table.

    Satisfies the :class:`~edu_it_domain.ports.repository.UserRepository`
    protocol via SQLAlchemy Core, so the application layer persists users exactly
    as it does against the in-memory fake.
    """

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL.

        A URL defers engine (and psycopg) creation until the first operation.
        """
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, user: UserRecord) -> None:
        """Insert ``user`` as a new ``user`` row.

        Raises :class:`ValueError` when the ``stable_id`` or email already
        exists: the pre-check keeps the error database-agnostic, and the
        ``stable_id`` unique constraint is the backstop that makes a concurrent
        duplicate fail rather than slip through.
        """
        if self.get_by_stable_id(user.stable_id) is not None:
            raise ValueError(f"user with stable_id {user.stable_id!r} already exists")
        if self.get_by_email(user.email) is not None:
            raise ValueError(f"user with email {user.email!r} already exists")
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    user_table.insert().values(
                        stable_id=user.stable_id,
                        email=user.email,
                        display_name=user.display_name,
                        locale=user.locale,
                        status=user.status,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError(f"user {user.stable_id!r} already exists") from exc

    def get_by_stable_id(self, stable_id: str) -> UserRecord | None:
        """Return the user with ``stable_id``, or ``None`` when absent."""
        return self._fetch(user_table.c.stable_id == stable_id)

    def get_by_email(self, email: str) -> UserRecord | None:
        """Return the user with ``email``, or ``None`` when absent."""
        return self._fetch(user_table.c.email == email)

    def mark_verified(self, stable_id: str) -> bool:
        """Transition the user for ``stable_id`` from unverified to verified.

        Returns whether an *unverified* user was transitioned: the UPDATE is
        guarded on the current status, so an unknown user or one already verified
        matches no row and yields ``False``. This makes verification idempotent
        at the store boundary and the condition the single gate for the
        unverified → verified transition (Req 7.4, 7.5).
        """
        with self._engine.begin() as connection:
            result = connection.execute(
                user_table.update()
                .where(
                    and_(
                        user_table.c.stable_id == stable_id,
                        user_table.c.status == USER_STATUS_UNVERIFIED,
                    )
                )
                .values(status=USER_STATUS_VERIFIED)
            )
        return result.rowcount == 1

    def _fetch(self, condition: object) -> UserRecord | None:
        """Fetch the single user matching ``condition`` (never its internal id)."""
        statement = select(
            user_table.c.stable_id,
            user_table.c.email,
            user_table.c.locale,
            user_table.c.status,
            user_table.c.display_name,
        ).where(condition)  # type: ignore[arg-type]
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        return UserRecord(
            stable_id=row["stable_id"],
            email=row["email"],
            locale=row["locale"],
            status=row["status"],
            display_name=row["display_name"],
        )


class Postgres_SessionRepository(SessionRepository):
    """A :class:`SessionRepository` backed by the PostgreSQL ``session`` table.

    Satisfies the :class:`~edu_it_domain.ports.repository.SessionRepository`
    protocol via SQLAlchemy Core. The durable record is the identity system of
    record (ADR 0004); the ephemeral fast-path state lives in Redis behind the
    ``SessionStore`` port. Sessions are joined to ``user`` by the internal
    foreign key so the domain only ever sees the owning user's ``stable_id``.
    """

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL.

        A URL defers engine (and psycopg) creation until the first operation.
        """
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, session: SessionRecord) -> None:
        """Insert ``session`` as a new ``session`` row.

        Resolves the owning user's internal id from its ``stable_id`` (the FK the
        schema stores) and raises :class:`ValueError` when the owner is unknown
        or a session already exists for the ``server_side_ref``.
        """
        if self.get(session.server_side_ref) is not None:
            raise ValueError(
                f"session with server_side_ref {session.server_side_ref!r} already exists"
            )
        with self._engine.begin() as connection:
            user_id = connection.execute(
                select(user_table.c.id).where(user_table.c.stable_id == session.user_stable_id)
            ).scalar_one_or_none()
            if user_id is None:
                raise ValueError(f"unknown owner user {session.user_stable_id!r}")
            connection.execute(
                session_table.insert().values(
                    user_id=user_id,
                    server_side_ref=session.server_side_ref,
                    expires_at=session.expires_at,
                    revoked_at=session.revoked_at,
                )
            )

    def get(self, server_side_ref: str) -> SessionRecord | None:
        """Return the session for ``server_side_ref``, or ``None`` when absent."""
        statement = (
            select(
                session_table.c.server_side_ref,
                user_table.c.stable_id.label("user_stable_id"),
                session_table.c.expires_at,
                session_table.c.revoked_at,
            )
            .select_from(session_table.join(user_table, session_table.c.user_id == user_table.c.id))
            .where(session_table.c.server_side_ref == server_side_ref)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        return SessionRecord(
            server_side_ref=row["server_side_ref"],
            user_stable_id=row["user_stable_id"],
            expires_at=_as_aware(row["expires_at"]),
            revoked_at=_as_aware_optional(row["revoked_at"]),
        )

    def revoke(self, server_side_ref: str, *, revoked_at: datetime) -> bool:
        """Mark the session for ``server_side_ref`` revoked at ``revoked_at``.

        Returns whether a *live* session was revoked: ``False`` when the session
        is unknown or already revoked, so revocation is idempotent.
        """
        with self._engine.begin() as connection:
            result = connection.execute(
                session_table.update()
                .where(
                    and_(
                        session_table.c.server_side_ref == server_side_ref,
                        session_table.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=revoked_at)
            )
        return result.rowcount == 1

    def revoke_all_for_user(self, user_stable_id: str, *, revoked_at: datetime) -> int:
        """Revoke every live session owned by ``user_stable_id``.

        Returns the number of sessions newly revoked (live sessions only). Used by
        password reset (Req 9.5) and account deletion (Req 14.3).
        """
        with self._engine.begin() as connection:
            user_id = connection.execute(
                select(user_table.c.id).where(user_table.c.stable_id == user_stable_id)
            ).scalar_one_or_none()
            if user_id is None:
                return 0
            result = connection.execute(
                session_table.update()
                .where(
                    and_(
                        session_table.c.user_id == user_id,
                        session_table.c.revoked_at.is_(None),
                    )
                )
                .values(revoked_at=revoked_at)
            )
        return int(result.rowcount)


class Postgres_RoleAssignmentRepository(RoleAssignmentRepository):
    """A :class:`RoleAssignmentRepository` backed by the ``role_assignment`` table.

    Satisfies the
    :class:`~edu_it_domain.ports.role_assignment.RoleAssignmentRepository`
    protocol via SQLAlchemy Core against the ``role_assignment`` table (Req 13).
    A user carries at most one role assignment: :meth:`set_role` is
    last-write-wins (it replaces any existing row for the user), so re-running the
    privileged-user CLI for the same user updates rather than accumulates
    (Req 13.2). The owning user is resolved by its ``stable_id`` to the internal
    foreign key the schema stores, so the domain never sees an internal key.
    """

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL.

        A URL defers engine (and psycopg) creation until the first operation.
        """
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def set_role(self, assignment: RoleAssignment) -> None:
        """Grant ``assignment``'s role to its user, replacing any prior grant.

        Resolves the owning user's internal id from its ``stable_id`` and raises
        :class:`ValueError` when the owner is unknown. The user's existing
        assignment rows are deleted first and the new one inserted in the same
        transaction, so the user carries exactly the most recently assigned role
        (last-write-wins, Req 13.2).
        """
        with self._engine.begin() as connection:
            user_id = connection.execute(
                select(user_table.c.id).where(user_table.c.stable_id == assignment.user_stable_id)
            ).scalar_one_or_none()
            if user_id is None:
                raise ValueError(f"unknown user {assignment.user_stable_id!r}")
            connection.execute(
                role_assignment_table.delete().where(role_assignment_table.c.user_id == user_id)
            )
            connection.execute(
                role_assignment_table.insert().values(
                    user_id=user_id,
                    role=assignment.role,
                    track_scope=assignment.track_scope,
                )
            )

    def get_for_user(self, user_stable_id: str) -> RoleAssignment | None:
        """Return the role assignment for ``user_stable_id``, or ``None``."""
        statement = (
            select(
                role_assignment_table.c.role,
                role_assignment_table.c.track_scope,
            )
            .select_from(
                role_assignment_table.join(
                    user_table, role_assignment_table.c.user_id == user_table.c.id
                )
            )
            .where(user_table.c.stable_id == user_stable_id)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        return RoleAssignment(
            user_stable_id=user_stable_id,
            role=row["role"],
            track_scope=row["track_scope"],
        )


def _as_aware(instant: datetime) -> datetime:
    """Return ``instant`` as an aware UTC datetime.

    SQLite returns naive datetimes even for timezone-aware columns; normalising
    to UTC here keeps the domain ``SessionRecord`` aware regardless of backend.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


def _as_aware_optional(instant: datetime | None) -> datetime | None:
    """Return an optional instant as aware UTC, preserving ``None``."""
    return None if instant is None else _as_aware(instant)
