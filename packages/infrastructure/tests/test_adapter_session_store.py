"""Unit tests for the Redis-backed Redis_SessionStore adapter (task 7.3, Req 5.1, 5.2, ADR 0004).

These exercise the ADR 0004 server-side session store against a Redis test double
(``fakeredis``) so no live Redis server is required, and against the controllable
``Test_Clock`` so expiry is deterministic. Coverage mirrors the port contract:
create, resolve by opaque reference, expiry measured by the Clock, and idempotent
revocation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from edu_it_domain.ports import SessionStore
from edu_it_infrastructure.adapters import Redis_SessionStore
from edu_it_infrastructure.fakes.clock import Test_Clock

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def clock() -> Test_Clock:
    c = Test_Clock()
    c.set(_T0)
    return c


def test_adapter_satisfies_the_domain_port(client: fakeredis.FakeRedis, clock: Test_Clock) -> None:
    # Substitutable for the in-memory fake without changing domain code (Req 5.2).
    assert isinstance(Redis_SessionStore(client, clock), SessionStore)


def test_create_then_resolve_returns_the_owner_and_expiry(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    store = Redis_SessionStore(client, clock)
    expires = _T0 + timedelta(hours=1)
    store.create("ref-1", "user-abc", expires_at=expires)
    resolved = store.resolve("ref-1")
    assert resolved is not None
    assert resolved.server_side_ref == "ref-1"
    assert resolved.user_stable_id == "user-abc"
    assert resolved.expires_at == expires


def test_resolving_an_unknown_reference_returns_none(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    store = Redis_SessionStore(client, clock)
    assert store.resolve("nope") is None


def test_session_expires_exactly_at_its_expiry_measured_by_the_clock(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    store = Redis_SessionStore(client, clock)
    expires = _T0 + timedelta(hours=1)
    store.create("ref", "user", expires_at=expires)

    # Just before expiry: still active.
    clock.set(expires - timedelta(seconds=1))
    assert store.resolve("ref") is not None

    # At expiry: no longer active (expiry is exclusive).
    clock.set(expires)
    assert store.resolve("ref") is None


def test_revoke_invalidates_the_session(client: fakeredis.FakeRedis, clock: Test_Clock) -> None:
    store = Redis_SessionStore(client, clock)
    store.create("ref", "user", expires_at=_T0 + timedelta(hours=1))
    assert store.revoke("ref") is True
    assert store.resolve("ref") is None


def test_revoke_is_idempotent(client: fakeredis.FakeRedis, clock: Test_Clock) -> None:
    # Req 8.2: sign-out succeeds even when no active session is present.
    store = Redis_SessionStore(client, clock)
    store.create("ref", "user", expires_at=_T0 + timedelta(hours=1))
    assert store.revoke("ref") is True
    assert store.revoke("ref") is False  # already revoked
    assert store.revoke("unknown") is False  # never existed


def test_revoking_an_expired_session_reports_no_active_session(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    store = Redis_SessionStore(client, clock)
    expires = _T0 + timedelta(hours=1)
    store.create("ref", "user", expires_at=expires)
    clock.set(expires + timedelta(seconds=1))
    assert store.revoke("ref") is False


def test_revoke_all_for_user_invalidates_only_that_users_active_sessions(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    # Req 9.5, 28.1: a completed reset revokes every ephemeral session the user
    # owns so none resolves afterward, while leaving other users untouched.
    store = Redis_SessionStore(client, clock)
    expires = _T0 + timedelta(hours=1)
    store.create("a", "user-1", expires_at=expires)
    store.create("b", "user-1", expires_at=expires)
    store.create("c", "user-2", expires_at=expires)

    assert store.revoke_all_for_user("user-1") == 2
    assert store.resolve("a") is None
    assert store.resolve("b") is None
    # Another user's session still resolves.
    assert store.resolve("c") is not None
    # Idempotent: a repeat revokes nothing new.
    assert store.revoke_all_for_user("user-1") == 0


def test_revoke_all_for_user_is_a_no_op_for_an_unknown_user(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    store = Redis_SessionStore(client, clock)
    assert store.revoke_all_for_user("ghost") == 0


def test_ownership_is_reverifiable_after_recreating_the_store(
    client: fakeredis.FakeRedis, clock: Test_Clock
) -> None:
    # ADR 0004: authoritative state is server-side, so a fresh store instance
    # pointed at the same Redis re-verifies ownership from the stored state.
    Redis_SessionStore(client, clock).create("ref", "owner", expires_at=_T0 + timedelta(hours=1))
    reread = Redis_SessionStore(client, clock).resolve("ref")
    assert reread is not None
    assert reread.user_stable_id == "owner"
