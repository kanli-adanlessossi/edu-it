"""Contract tests for the credential and verification-link ports (api-route-wiring 2.3).

One parametrized suite per port runs against the in-memory fake and the SQL adapter:
SQLite always, PostgreSQL additionally when ``DATABASE_URL`` is set. The concurrent
``mark_consumed`` case is PostgreSQL-only (SQLite serializes writers, so it proves
nothing there); it is skipped without ``DATABASE_URL`` and runs in CI against a
PostgreSQL service container. Migration up/down is covered in
``test_migration_accounts_runtime.py``.

Validates: Requirements 7.5, 7.6, 7.8, 7.9, 7.12

Task 15.3 extends the suites to the reset-link, pending-OAuth-link and OAuth-link ports.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from edu_it_domain.ports import UserRecord
from edu_it_domain.ports.credential_store import CredentialStore
from edu_it_domain.ports.oauth_link import OAuthLink, OAuthLinkRepository
from edu_it_domain.ports.pending_oauth_link import PendingOAuthLink, PendingOAuthLinkRepository
from edu_it_domain.ports.reset_link import ResetLink, ResetLinkRepository
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)
from edu_it_infrastructure.fakes.repositories import (
    In_Memory_CredentialStore,
    In_Memory_OAuthLinkRepository,
    In_Memory_PendingOAuthLinkRepository,
    In_Memory_ResetLinkRepository,
    In_Memory_VerificationLinkRepository,
)
from edu_it_infrastructure.persistence import Postgres_UserRepository, metadata
from edu_it_infrastructure.persistence.account_repositories import (
    Postgres_CredentialStore,
    Postgres_OAuthLinkRepository,
    Postgres_PendingOAuthLinkRepository,
    Postgres_ResetLinkRepository,
    Postgres_VerificationLinkRepository,
    token_digest,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES_URL = _DATABASE_URL if _DATABASE_URL.startswith("postgresql") else ""

_BACKENDS = [
    "fake",
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(not _POSTGRES_URL, reason="DATABASE_URL (PostgreSQL) not set"),
    ),
]


class _EqualityVerifier:
    """A verifier comparing the raw value to the stored secret (as the fake does)."""

    def verify(self, raw: str, stored: str | None) -> bool:
        return stored is not None and raw == stored


@dataclass
class _Backend:
    links: VerificationLinkRepository
    credentials: CredentialStore
    new_user: Callable[[], str]
    engine: Engine | None
    resets: ResetLinkRepository
    pendings: PendingOAuthLinkRepository
    oauth: OAuthLinkRepository


def _sqlite_engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(eng)
    return eng


def _sql_backend(engine: Engine) -> _Backend:
    users = Postgres_UserRepository(engine)

    def new_user() -> str:
        stable_id = f"u-{uuid.uuid4().hex[:24]}"
        users.add(
            UserRecord(
                stable_id=stable_id,
                email=f"{stable_id}@example.test",
                locale="en",
                status="unverified",
                display_name="T",
            )
        )
        return stable_id

    return _Backend(
        Postgres_VerificationLinkRepository(engine),
        Postgres_CredentialStore(engine, _EqualityVerifier()),
        new_user,
        engine,
        Postgres_ResetLinkRepository(engine),
        Postgres_PendingOAuthLinkRepository(engine),
        Postgres_OAuthLinkRepository(engine),
    )


@pytest.fixture(params=_BACKENDS)
def backend(request: pytest.FixtureRequest) -> Iterator[_Backend]:
    if request.param == "fake":
        yield _Backend(
            In_Memory_VerificationLinkRepository(),
            In_Memory_CredentialStore(),
            lambda: f"u-{uuid.uuid4().hex[:24]}",
            None,
            In_Memory_ResetLinkRepository(),
            In_Memory_PendingOAuthLinkRepository(),
            In_Memory_OAuthLinkRepository(),
        )
        return
    engine = _sqlite_engine() if request.param == "sqlite" else create_engine(_POSTGRES_URL)
    if request.param == "postgres":
        metadata.create_all(engine)
    try:
        yield _sql_backend(engine)
    finally:
        engine.dispose()


def _link(user: str, token: str | None = None, **kw: object) -> VerificationLink:
    return VerificationLink(
        token=token or uuid.uuid4().hex,
        user_stable_id=user,
        issued_at=_T0,
        expires_at=_T0 + timedelta(hours=24),
        **kw,  # type: ignore[arg-type]
    )


# --- VerificationLinkRepository ---------------------------------------------


def test_link_add_get_roundtrip_and_unknown(backend: _Backend) -> None:
    user = backend.new_user()
    link = _link(user)
    backend.links.add(link)
    assert backend.links.get(link.token) == link
    assert backend.links.get("unknown-" + uuid.uuid4().hex) is None


def test_link_add_rejects_reused_token(backend: _Backend) -> None:
    user = backend.new_user()
    link = _link(user)
    backend.links.add(link)
    with pytest.raises(ValueError):
        backend.links.add(link)


def test_link_mark_consumed_sequential_single_use(backend: _Backend) -> None:
    link = _link(backend.new_user())
    backend.links.add(link)
    t1 = _T0 + timedelta(minutes=1)
    assert backend.links.mark_consumed(link.token, consumed_at=t1) is True
    assert backend.links.mark_consumed(link.token, consumed_at=t1 + timedelta(hours=1)) is False
    stored = backend.links.get(link.token)
    assert stored is not None and stored.consumed_at == t1
    assert backend.links.mark_consumed("missing-" + uuid.uuid4().hex, consumed_at=t1) is False


def test_link_token_stored_only_as_digest(backend: _Backend) -> None:
    if backend.engine is None:
        pytest.skip("digest storage applies to the SQL adapter")
    from edu_it_infrastructure.persistence.metadata import verification_link

    link = _link(backend.new_user())
    backend.links.add(link)
    with backend.engine.connect() as conn:
        rows = conn.execute(
            select(verification_link).where(
                verification_link.c.token_digest == token_digest(link.token)
            )
        ).fetchall()
    assert len(rows) == 1
    assert link.token not in [str(v) for v in rows[0]]


def _reset(user: str, token: str | None = None) -> ResetLink:
    return ResetLink(
        token=token or uuid.uuid4().hex,
        user_stable_id=user,
        issued_at=_T0,
        expires_at=_T0 + timedelta(hours=1),
    )


def _pending(user: str, token: str | None = None) -> PendingOAuthLink:
    return PendingOAuthLink(
        token=token or uuid.uuid4().hex,
        provider="github",
        subject="s-" + uuid.uuid4().hex[:12],
        user_stable_id=user,
        email=f"{user}@example.test",
        issued_at=_T0,
        expires_at=_T0 + timedelta(minutes=15),
    )


# --- ResetLinkRepository (Req 7.6, 7.8, 7.9) ---------------------------------


def test_reset_add_get_roundtrip_and_unknown(backend: _Backend) -> None:
    link = _reset(backend.new_user())
    backend.resets.add(link)
    assert backend.resets.get(link.token) == link
    assert backend.resets.get("unknown-" + uuid.uuid4().hex) is None


def test_reset_add_rejects_reused_token(backend: _Backend) -> None:
    link = _reset(backend.new_user())
    backend.resets.add(link)
    with pytest.raises(ValueError):
        backend.resets.add(link)


def test_reset_mark_consumed_sequential_single_use(backend: _Backend) -> None:
    link = _reset(backend.new_user())
    backend.resets.add(link)
    t1 = _T0 + timedelta(minutes=1)
    assert backend.resets.mark_consumed(link.token, consumed_at=t1) is True
    assert backend.resets.mark_consumed(link.token, consumed_at=t1 + timedelta(minutes=5)) is False
    stored = backend.resets.get(link.token)
    assert stored is not None and stored.consumed_at == t1
    assert backend.resets.mark_consumed("missing-" + uuid.uuid4().hex, consumed_at=t1) is False


def test_reset_token_stored_only_as_digest(backend: _Backend) -> None:
    if backend.engine is None:
        pytest.skip("digest storage applies to the SQL adapter")
    from edu_it_infrastructure.persistence.metadata import reset_link

    link = _reset(backend.new_user())
    backend.resets.add(link)
    with backend.engine.connect() as conn:
        rows = conn.execute(
            select(reset_link).where(reset_link.c.token_digest == token_digest(link.token))
        ).fetchall()
    assert len(rows) == 1
    assert link.token not in [str(v) for v in rows[0]]


# --- PendingOAuthLinkRepository (Req 7.6, 7.8, 7.9) --------------------------


def test_pending_add_get_roundtrip_and_unknown(backend: _Backend) -> None:
    pending = _pending(backend.new_user())
    backend.pendings.add(pending)
    assert backend.pendings.get(pending.token) == pending
    assert backend.pendings.get("unknown-" + uuid.uuid4().hex) is None


def test_pending_add_rejects_reused_token(backend: _Backend) -> None:
    pending = _pending(backend.new_user())
    backend.pendings.add(pending)
    with pytest.raises(ValueError):
        backend.pendings.add(pending)


def test_pending_mark_consumed_sequential_single_use(backend: _Backend) -> None:
    pending = _pending(backend.new_user())
    backend.pendings.add(pending)
    t1 = _T0 + timedelta(minutes=1)
    assert backend.pendings.mark_consumed(pending.token, consumed_at=t1) is True
    later = t1 + timedelta(minutes=2)
    assert backend.pendings.mark_consumed(pending.token, consumed_at=later) is False
    stored = backend.pendings.get(pending.token)
    assert stored is not None and stored.consumed_at == t1
    assert backend.pendings.mark_consumed("missing-" + uuid.uuid4().hex, consumed_at=t1) is False


def test_pending_token_stored_only_as_digest(backend: _Backend) -> None:
    if backend.engine is None:
        pytest.skip("digest storage applies to the SQL adapter")
    from edu_it_infrastructure.persistence.metadata import pending_oauth_link

    pending = _pending(backend.new_user())
    backend.pendings.add(pending)
    with backend.engine.connect() as conn:
        rows = conn.execute(
            select(pending_oauth_link).where(
                pending_oauth_link.c.token_digest == token_digest(pending.token)
            )
        ).fetchall()
    assert len(rows) == 1
    assert pending.token not in [str(v) for v in rows[0]]


# --- OAuthLinkRepository (Req 7.12) ------------------------------------------


def _oauth(backend: _Backend, subject: str, provider: str = "github") -> OAuthLink:
    return OAuthLink(provider=provider, subject=subject, user_stable_id=backend.new_user())


def test_oauth_add_get_roundtrip_and_unknown(backend: _Backend) -> None:
    link = _oauth(backend, "s-" + uuid.uuid4().hex)
    backend.oauth.add(link)
    assert backend.oauth.get(link.provider, link.subject) == link
    assert backend.oauth.get("github", "unknown-" + uuid.uuid4().hex) is None


def test_oauth_add_rejects_duplicate_identity(backend: _Backend) -> None:
    subject = "s-" + uuid.uuid4().hex
    backend.oauth.add(_oauth(backend, subject))
    with pytest.raises(ValueError):
        backend.oauth.add(_oauth(backend, subject))


def test_oauth_same_subject_other_provider_is_distinct(backend: _Backend) -> None:
    subject = "s-" + uuid.uuid4().hex
    backend.oauth.add(_oauth(backend, subject))
    backend.oauth.add(_oauth(backend, subject, provider="other"))
    assert backend.oauth.get("other", subject) is not None

# --- CredentialStore ---------------------------------------------------------


def test_credential_set_verify_replace(backend: _Backend) -> None:
    user = backend.new_user()
    assert backend.credentials.verify_password(user, "secret") is False  # no credential
    backend.credentials.set_password(user, "secret")
    assert backend.credentials.verify_password(user, "secret") is True
    assert backend.credentials.verify_password(user, "wrong") is False
    backend.credentials.set_password(user, "other")
    assert backend.credentials.verify_password(user, "secret") is False
    assert backend.credentials.verify_password(user, "other") is True


def test_credential_unknown_user_never_verifies(backend: _Backend) -> None:
    assert backend.credentials.verify_password("nobody-" + uuid.uuid4().hex, "x") is False


# --- Concurrency (PostgreSQL only) ------------------------------------------


@pytest.mark.skipif(not _POSTGRES_URL, reason="concurrent consume needs PostgreSQL (DATABASE_URL)")
@pytest.mark.parametrize("consumers", [2, 5, 20])
def test_concurrent_mark_consumed_exactly_one_wins(consumers: int) -> None:
    engine = create_engine(_POSTGRES_URL, pool_size=consumers, max_overflow=0)
    metadata.create_all(engine)
    try:
        be = _sql_backend(engine)
        link = _link(be.new_user())
        be.links.add(link)
        barrier = Barrier(consumers)

        def consume(i: int) -> bool:
            barrier.wait()
            return be.links.mark_consumed(link.token, consumed_at=_T0 + timedelta(seconds=i))

        with ThreadPoolExecutor(max_workers=consumers) as pool:
            results = list(pool.map(consume, range(consumers)))
        assert results.count(True) == 1
        assert results.count(False) == consumers - 1
    finally:
        engine.dispose()


@pytest.mark.skipif(not _POSTGRES_URL, reason="concurrent consume needs PostgreSQL (DATABASE_URL)")
@pytest.mark.parametrize("kind", ["reset", "pending"])
@pytest.mark.parametrize("consumers", [2, 5, 20])
def test_concurrent_mark_consumed_reset_and_pending_exactly_one_wins(
    kind: str, consumers: int
) -> None:
    engine = create_engine(_POSTGRES_URL, pool_size=consumers, max_overflow=0)
    metadata.create_all(engine)
    try:
        be = _sql_backend(engine)
        user = be.new_user()
        token = uuid.uuid4().hex
        repo: ResetLinkRepository | PendingOAuthLinkRepository
        if kind == "reset":
            repo = be.resets
            be.resets.add(_reset(user, token))
        else:
            repo = be.pendings
            be.pendings.add(_pending(user, token))
        barrier = Barrier(consumers)

        def consume(i: int) -> bool:
            barrier.wait()
            return repo.mark_consumed(token, consumed_at=_T0 + timedelta(seconds=i))

        with ThreadPoolExecutor(max_workers=consumers) as pool:
            results = list(pool.map(consume, range(consumers)))
        assert results.count(True) == 1
        assert results.count(False) == consumers - 1
    finally:
        engine.dispose()


# --- Property 11 -------------------------------------------------------------

# Feature: api-route-wiring, Property 11: Link tokens are stored as digests and consumed once
# Validates: Requirements 7.5, 7.6, 7.8
@settings(max_examples=100, deadline=None)
@given(
    token=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=8,
        max_size=64,
    ),
    attempts=st.integers(min_value=2, max_value=6),
)
def test_property_11_tokens_stored_as_digests_and_consumed_once(token: str, attempts: int) -> None:
    from edu_it_infrastructure.persistence.metadata import verification_link

    engine = _sqlite_engine()
    try:
        be = _sql_backend(engine)
        link = _link(be.new_user(), token=token)
        be.links.add(link)

        with engine.connect() as conn:
            rows = conn.execute(select(verification_link)).fetchall()
        assert len(rows) == 1
        stored = [str(v) for v in rows[0]]
        assert token_digest(token) in stored
        assert token not in stored

        outcomes = [
            be.links.mark_consumed(token, consumed_at=_T0 + timedelta(seconds=i))
            for i in range(attempts)
        ]
        assert outcomes == [True] + [False] * (attempts - 1)
        got = be.links.get(token)
        assert got is not None and got.consumed_at == _T0
    finally:
        engine.dispose()


# Feature: api-route-wiring, Property 11: Link tokens are stored as digests and consumed once
# Validates: Requirements 7.6, 7.8, 7.9, 7.12
@settings(max_examples=100, deadline=None)
@given(
    token=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        min_size=8,
        max_size=64,
    ),
    attempts=st.integers(min_value=2, max_value=6),
    kind=st.sampled_from(["reset", "pending"]),
)
def test_property_11_reset_and_pending_tokens_digest_only_consumed_once(
    token: str, attempts: int, kind: str
) -> None:
    from edu_it_infrastructure.persistence.metadata import pending_oauth_link, reset_link

    engine = _sqlite_engine()
    try:
        be = _sql_backend(engine)
        user = be.new_user()
        repo: ResetLinkRepository | PendingOAuthLinkRepository
        if kind == "reset":
            repo = be.resets
            be.resets.add(_reset(user, token))
            table = reset_link
        else:
            repo = be.pendings
            be.pendings.add(_pending(user, token))
            table = pending_oauth_link

        with engine.connect() as conn:
            rows = conn.execute(select(table)).fetchall()
        assert len(rows) == 1
        stored = [str(v) for v in rows[0]]
        assert token_digest(token) in stored
        assert token not in stored

        outcomes = [
            repo.mark_consumed(token, consumed_at=_T0 + timedelta(seconds=i))
            for i in range(attempts)
        ]
        assert outcomes == [True] + [False] * (attempts - 1)
        got = repo.get(token)
        assert got is not None and got.consumed_at == _T0
    finally:
        engine.dispose()