"""PostgreSQL adapters for the password credential and verification links.

:class:`Postgres_CredentialStore` and :class:`Postgres_VerificationLinkRepository`
implement the domain ``CredentialStore`` and ``VerificationLinkRepository`` ports
over the ``credential`` and ``verification_link`` tables (revision
``0003_accounts_runtime``). They follow the style of
:mod:`~edu_it_infrastructure.persistence.repositories`: SQLAlchemy Core, one short
transaction per operation, no internal numeric key surfaced to the domain.

Only the SHA-256 hex digest of a link token is stored; the raw token never reaches
the database. ``mark_consumed`` is a single conditional ``UPDATE`` so a link is
consumed exactly once even under concurrent consumers (Req 7.6, 7.8). The stored
credential is an already-hashed secret; verification is delegated to the injected
verifier (the ``Scrypt_PasswordHasher``), which burns equal work when no credential
exists (Req 7.2).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.oauth_link import OAuthLink, OAuthLinkRepository
from edu_it_domain.ports.pending_oauth_link import (
    PendingOAuthLink,
    PendingOAuthLinkRepository,
)
from edu_it_domain.ports.reset_link import ResetLink, ResetLinkRepository
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)
from sqlalchemy import Table, and_, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from edu_it_infrastructure.persistence.metadata import credential as credential_table
from edu_it_infrastructure.persistence.metadata import oauth_link as oauth_link_table
from edu_it_infrastructure.persistence.metadata import (
    pending_oauth_link as pending_oauth_link_table,
)
from edu_it_infrastructure.persistence.metadata import reset_link as reset_link_table
from edu_it_infrastructure.persistence.metadata import user as user_table
from edu_it_infrastructure.persistence.metadata import (
    verification_link as verification_link_table,
)
from edu_it_infrastructure.persistence.repositories import _EngineHandle

__all__ = [
    "PasswordVerifier",
    "Postgres_CredentialStore",
    "Postgres_OAuthLinkRepository",
    "Postgres_PendingOAuthLinkRepository",
    "Postgres_ResetLinkRepository",
    "Postgres_VerificationLinkRepository",
    "token_digest",
]


class PasswordVerifier(Protocol):
    """The slice of the password hasher the credential store needs."""

    def verify(self, raw: str, stored: str | None) -> bool:
        """Return whether ``raw`` matches ``stored`` (equal work when missing)."""
        ...


def token_digest(token: str) -> str:
    """Return the hex SHA-256 digest of ``token`` (the only form stored)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    """Return ``value`` as aware UTC (SQLite drops the zone on read)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _user_id(connection: Connection, stable_id: str) -> int | None:
    """Return the internal ``user.id`` for ``stable_id``, or ``None``."""
    value = connection.execute(
        select(user_table.c.id).where(user_table.c.stable_id == stable_id)
    ).scalar()
    return None if value is None else int(value)


class Postgres_CredentialStore(CredentialStore):
    """A :class:`CredentialStore` backed by the ``credential`` table."""

    def __init__(self, engine: Engine | str, verifier: PasswordVerifier) -> None:
        """Create the store over ``engine`` (or URL) and a password ``verifier``."""
        self._handle = _EngineHandle(engine)
        self._verifier = verifier

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        """Store or replace the already-hashed credential for a user.

        Raises :class:`ValueError` when no user has ``user_stable_id``.
        """
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            user_id = _user_id(connection, user_stable_id)
            if user_id is None:
                raise ValueError(f"user {user_stable_id!r} does not exist")
            result = connection.execute(
                credential_table.update()
                .where(credential_table.c.user_id == user_id)
                .values(secret=hashed_secret, updated_at=now)
            )
            if result.rowcount == 0:
                connection.execute(
                    credential_table.insert().values(
                        user_id=user_id, secret=hashed_secret, updated_at=now
                    )
                )

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        """Return whether ``password`` matches the stored credential.

        A missing user or credential is verified against a dummy hash by the
        verifier, so the result and the work are the same as for a wrong password.
        """
        statement = (
            select(credential_table.c.secret)
            .select_from(
                credential_table.join(user_table, credential_table.c.user_id == user_table.c.id)
            )
            .where(user_table.c.stable_id == user_stable_id)
        )
        with self._engine.connect() as connection:
            stored = connection.execute(statement).scalar()
        return self._verifier.verify(password, stored)


class Postgres_VerificationLinkRepository(VerificationLinkRepository):
    """A :class:`VerificationLinkRepository` backed by ``verification_link``."""

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL."""
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, link: VerificationLink) -> None:
        """Store ``link`` keyed by the digest of its token.

        Raises :class:`ValueError` when the token was already used for a link or
        the owning user does not exist.
        """
        digest = token_digest(link.token)
        try:
            with self._engine.begin() as connection:
                user_id = _user_id(connection, link.user_stable_id)
                if user_id is None:
                    raise ValueError(f"user {link.user_stable_id!r} does not exist")
                exists = connection.execute(
                    select(verification_link_table.c.id).where(
                        verification_link_table.c.token_digest == digest
                    )
                ).first()
                if exists is not None:
                    raise ValueError("verification link for this token already exists")
                connection.execute(
                    verification_link_table.insert().values(
                        token_digest=digest,
                        user_id=user_id,
                        issued_at=link.issued_at,
                        expires_at=link.expires_at,
                        consumed_at=link.consumed_at,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError("verification link for this token already exists") from exc

    def get(self, token: str) -> VerificationLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized."""
        statement = (
            select(
                user_table.c.stable_id,
                verification_link_table.c.issued_at,
                verification_link_table.c.expires_at,
                verification_link_table.c.consumed_at,
            )
            .select_from(
                verification_link_table.join(
                    user_table, verification_link_table.c.user_id == user_table.c.id
                )
            )
            .where(verification_link_table.c.token_digest == token_digest(token))
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        issued_at = _aware(row["issued_at"])
        expires_at = _aware(row["expires_at"])
        assert issued_at is not None and expires_at is not None
        return VerificationLink(
            token=token,
            user_stable_id=row["stable_id"],
            issued_at=issued_at,
            expires_at=expires_at,
            consumed_at=_aware(row["consumed_at"]),
        )

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Consume the link for ``token`` with one conditional ``UPDATE``.

        Returns whether exactly one unused row changed; unknown or already
        consumed tokens match no row and yield ``False``.
        """
        with self._engine.begin() as connection:
            result = connection.execute(
                verification_link_table.update()
                .where(
                    and_(
                        verification_link_table.c.token_digest == token_digest(token),
                        verification_link_table.c.consumed_at.is_(None),
                    )
                )
                .values(consumed_at=consumed_at)
            )
        return result.rowcount == 1


def _consume(engine: Engine, table: Table, token: str, consumed_at: datetime) -> bool:
    """Consume the row for ``token`` with one conditional ``UPDATE``."""
    with engine.begin() as connection:
        result = connection.execute(
            table.update()
            .where(
                and_(
                    table.c.token_digest == token_digest(token),
                    table.c.consumed_at.is_(None),
                )
            )
            .values(consumed_at=consumed_at)
        )
    return result.rowcount == 1


class Postgres_ResetLinkRepository(ResetLinkRepository):
    """A :class:`ResetLinkRepository` backed by ``reset_link`` (digest only)."""

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL."""
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, link: ResetLink) -> None:
        """Store ``link`` keyed by the digest of its token.

        Raises :class:`ValueError` when the token is already used or the user is
        unknown.
        """
        digest = token_digest(link.token)
        try:
            with self._engine.begin() as connection:
                user_id = _user_id(connection, link.user_stable_id)
                if user_id is None:
                    raise ValueError(f"user {link.user_stable_id!r} does not exist")
                exists = connection.execute(
                    select(reset_link_table.c.id).where(reset_link_table.c.token_digest == digest)
                ).first()
                if exists is not None:
                    raise ValueError("reset link for this token already exists")
                connection.execute(
                    reset_link_table.insert().values(
                        token_digest=digest,
                        user_id=user_id,
                        issued_at=link.issued_at,
                        expires_at=link.expires_at,
                        consumed_at=link.consumed_at,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError("reset link for this token already exists") from exc

    def get(self, token: str) -> ResetLink | None:
        """Return the link for ``token``, or ``None`` when unrecognized."""
        statement = (
            select(
                user_table.c.stable_id,
                reset_link_table.c.issued_at,
                reset_link_table.c.expires_at,
                reset_link_table.c.consumed_at,
            )
            .select_from(
                reset_link_table.join(user_table, reset_link_table.c.user_id == user_table.c.id)
            )
            .where(reset_link_table.c.token_digest == token_digest(token))
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        issued_at = _aware(row["issued_at"])
        expires_at = _aware(row["expires_at"])
        assert issued_at is not None and expires_at is not None
        return ResetLink(
            token=token,
            user_stable_id=row["stable_id"],
            issued_at=issued_at,
            expires_at=expires_at,
            consumed_at=_aware(row["consumed_at"]),
        )

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Consume the link with one conditional ``UPDATE``; ``True`` if it changed."""
        return _consume(self._engine, reset_link_table, token, consumed_at)


class Postgres_PendingOAuthLinkRepository(PendingOAuthLinkRepository):
    """A :class:`PendingOAuthLinkRepository` backed by ``pending_oauth_link``."""

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL."""
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, pending: PendingOAuthLink) -> None:
        """Store ``pending`` keyed by the digest of its token.

        Raises :class:`ValueError` when the token is already used or the user is
        unknown.
        """
        digest = token_digest(pending.token)
        try:
            with self._engine.begin() as connection:
                user_id = _user_id(connection, pending.user_stable_id)
                if user_id is None:
                    raise ValueError(f"user {pending.user_stable_id!r} does not exist")
                exists = connection.execute(
                    select(pending_oauth_link_table.c.id).where(
                        pending_oauth_link_table.c.token_digest == digest
                    )
                ).first()
                if exists is not None:
                    raise ValueError("pending OAuth link for this token already exists")
                connection.execute(
                    pending_oauth_link_table.insert().values(
                        token_digest=digest,
                        provider=pending.provider,
                        subject=pending.subject,
                        user_id=user_id,
                        email=pending.email,
                        issued_at=pending.issued_at,
                        expires_at=pending.expires_at,
                        consumed_at=pending.consumed_at,
                    )
                )
        except IntegrityError as exc:  # pragma: no cover - concurrent-insert guard
            raise ValueError("pending OAuth link for this token already exists") from exc

    def get(self, token: str) -> PendingOAuthLink | None:
        """Return the pending link for ``token``, or ``None`` when unrecognized."""
        table = pending_oauth_link_table
        statement = (
            select(
                user_table.c.stable_id,
                table.c.provider,
                table.c.subject,
                table.c.email,
                table.c.issued_at,
                table.c.expires_at,
                table.c.consumed_at,
            )
            .select_from(table.join(user_table, table.c.user_id == user_table.c.id))
            .where(table.c.token_digest == token_digest(token))
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        issued_at = _aware(row["issued_at"])
        expires_at = _aware(row["expires_at"])
        assert issued_at is not None and expires_at is not None
        return PendingOAuthLink(
            token=token,
            provider=row["provider"],
            subject=row["subject"],
            user_stable_id=row["stable_id"],
            email=row["email"],
            issued_at=issued_at,
            expires_at=expires_at,
            consumed_at=_aware(row["consumed_at"]),
        )

    def mark_consumed(self, token: str, *, consumed_at: datetime) -> bool:
        """Consume the record with one conditional ``UPDATE``; ``True`` if it changed."""
        return _consume(self._engine, pending_oauth_link_table, token, consumed_at)


class Postgres_OAuthLinkRepository(OAuthLinkRepository):
    """An :class:`OAuthLinkRepository` backed by the ``oauth_link`` table."""

    def __init__(self, engine: Engine | str) -> None:
        """Create the repository over an existing ``engine`` or a database URL."""
        self._handle = _EngineHandle(engine)

    @property
    def _engine(self) -> Engine:
        return self._handle.engine

    def add(self, link: OAuthLink) -> None:
        """Store ``link``.

        Raises :class:`ValueError` when ``(provider, subject)`` is already linked
        or the user is unknown.
        """
        table = oauth_link_table
        with self._engine.begin() as connection:
            user_id = _user_id(connection, link.user_stable_id)
            if user_id is None:
                raise ValueError(f"user {link.user_stable_id!r} does not exist")
            exists = connection.execute(
                select(table.c.id).where(
                    and_(table.c.provider == link.provider, table.c.subject == link.subject)
                )
            ).first()
            if exists is not None:
                raise ValueError("an OAuth link for this identity already exists")
            connection.execute(
                table.insert().values(
                    user_id=user_id, provider=link.provider, subject=link.subject
                )
            )

    def get(self, provider: str, subject: str) -> OAuthLink | None:
        """Return the link for ``(provider, subject)``, or ``None`` when absent."""
        table = oauth_link_table
        statement = (
            select(user_table.c.stable_id)
            .select_from(table.join(user_table, table.c.user_id == user_table.c.id))
            .where(and_(table.c.provider == provider, table.c.subject == subject))
        )
        with self._engine.connect() as connection:
            stable_id = connection.execute(statement).scalar()
        if stable_id is None:
            return None
        return OAuthLink(provider=provider, subject=subject, user_stable_id=stable_id)
