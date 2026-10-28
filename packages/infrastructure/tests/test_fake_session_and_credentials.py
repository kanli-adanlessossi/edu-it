"""Unit tests for the session/credential fakes added for sign-in (task 13.1, Req 5.2, 8).

These assert the three in-memory fakes introduced for the sign-in / sign-out /
session-authentication use cases are substitutable for their production
counterparts (Req 5.2) and honour the ADR 0004 session semantics the use cases
rely on:

- :class:`In_Memory_CredentialStore` satisfies the ``CredentialStore`` port and
  verifies non-disclosingly (no stored credential is indistinguishable from a
  wrong password, Req 8.4).
- :class:`In_Memory_SessionRepository` satisfies the ``SessionRepository`` port
  and revokes idempotently (Req 8.2) including bulk revocation (Req 9.5, 14.3).
- :class:`In_Memory_SessionStore` satisfies the ``SessionStore`` port, decides
  activeness against the injected Clock (Req 8.5), and flags revocation
  idempotently (Req 8.2), mirroring :class:`Redis_SessionStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.ports import (
    CredentialStore,
    SessionRecord,
    SessionRepository,
    SessionStore,
)
from edu_it_infrastructure.fakes import (
    In_Memory_CredentialStore,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


# --- CredentialStore fake ----------------------------------------------------


def test_credential_store_satisfies_the_domain_port() -> None:
    assert isinstance(In_Memory_CredentialStore(), CredentialStore)


def test_credential_store_verifies_stored_secret() -> None:
    store = In_Memory_CredentialStore()
    store.set_password("user-1", "hash:secret")
    assert store.verify_password("user-1", "hash:secret") is True
    assert store.verify_password("user-1", "hash:other") is False


def test_credential_store_non_disclosing_for_unknown_user() -> None:
    # Req 8.4: no stored credential is indistinguishable from a wrong password.
    store = In_Memory_CredentialStore()
    assert store.verify_password("unknown", "anything") is False


def test_credential_store_set_replaces_existing_secret() -> None:
    store = In_Memory_CredentialStore()
    store.set_password("user-1", "hash:old")
    store.set_password("user-1", "hash:new")
    assert store.verify_password("user-1", "hash:old") is False
    assert store.verify_password("user-1", "hash:new") is True


# --- SessionRepository fake --------------------------------------------------


def _record(ref: str = "ref-1", user: str = "user-1") -> SessionRecord:
    return SessionRecord(
        server_side_ref=ref,
        user_stable_id=user,
        expires_at=_T0 + timedelta(days=14),
    )


def test_session_repository_satisfies_the_domain_port() -> None:
    assert isinstance(In_Memory_SessionRepository(), SessionRepository)


def test_session_repository_add_and_get_round_trip() -> None:
    repo = In_Memory_SessionRepository()
    repo.add(_record())
    stored = repo.get("ref-1")
    assert stored is not None
    assert stored.user_stable_id == "user-1"
    assert repo.get("absent") is None


def test_session_repository_revoke_is_idempotent() -> None:
    repo = In_Memory_SessionRepository()
    repo.add(_record())
    assert repo.revoke("ref-1", revoked_at=_T0) is True
    assert repo.revoke("ref-1", revoked_at=_T0) is False
    assert repo.revoke("absent", revoked_at=_T0) is False
    stored = repo.get("ref-1")
    assert stored is not None
    assert stored.is_revoked


def test_session_repository_revoke_all_for_user_counts_live_sessions() -> None:
    repo = In_Memory_SessionRepository()
    repo.add(_record(ref="a", user="user-1"))
    repo.add(_record(ref="b", user="user-1"))
    repo.add(_record(ref="c", user="user-2"))
    # Only user-1's two live sessions are revoked.
    assert repo.revoke_all_for_user("user-1", revoked_at=_T0) == 2
    # A repeat revokes nothing (both already revoked).
    assert repo.revoke_all_for_user("user-1", revoked_at=_T0) == 0
    other = repo.get("c")
    assert other is not None and not other.is_revoked


# --- SessionStore fake -------------------------------------------------------


def test_session_store_satisfies_the_domain_port() -> None:
    assert isinstance(In_Memory_SessionStore(Test_Clock(start=_T0)), SessionStore)


def test_session_store_resolves_an_active_session() -> None:
    clock = Test_Clock(start=_T0)
    store = In_Memory_SessionStore(clock)
    store.create("ref", "user", expires_at=_T0 + timedelta(hours=1))
    resolved = store.resolve("ref")
    assert resolved is not None
    assert resolved.user_stable_id == "user"


def test_session_store_resolve_returns_none_for_unknown_reference() -> None:
    store = In_Memory_SessionStore(Test_Clock(start=_T0))
    assert store.resolve("nope") is None


def test_session_store_expiry_is_measured_by_the_clock() -> None:
    # Req 8.5: an expired session resolves to None; expiry is strict (>=).
    clock = Test_Clock(start=_T0)
    store = In_Memory_SessionStore(clock)
    expires = _T0 + timedelta(hours=1)
    store.create("ref", "user", expires_at=expires)
    clock.set(expires - timedelta(seconds=1))
    assert store.resolve("ref") is not None
    clock.set(expires)
    assert store.resolve("ref") is None


def test_session_store_revoke_is_idempotent() -> None:
    # Req 8.2: revoking invalidates; re-revoking is a safe no-op.
    store = In_Memory_SessionStore(Test_Clock(start=_T0))
    store.create("ref", "user", expires_at=_T0 + timedelta(hours=1))
    assert store.revoke("ref") is True
    assert store.resolve("ref") is None
    assert store.revoke("ref") is False
    assert store.revoke("absent") is False


def test_session_store_revoke_all_for_user_invalidates_only_that_users_active_sessions() -> None:
    # Req 9.5, 28.1: a completed reset revokes every ephemeral session the user
    # owns so none resolves afterward, while leaving other users untouched.
    clock = Test_Clock(start=_T0)
    store = In_Memory_SessionStore(clock)
    store.create("a", "user-1", expires_at=_T0 + timedelta(hours=1))
    store.create("b", "user-1", expires_at=_T0 + timedelta(hours=1))
    store.create("c", "user-2", expires_at=_T0 + timedelta(hours=1))

    assert store.revoke_all_for_user("user-1") == 2
    assert store.resolve("a") is None
    assert store.resolve("b") is None
    # Another user's session still resolves.
    assert store.resolve("c") is not None
    # Idempotent: a repeat revokes nothing new.
    assert store.revoke_all_for_user("user-1") == 0
