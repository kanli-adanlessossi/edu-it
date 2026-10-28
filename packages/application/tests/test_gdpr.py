"""Unit tests for the GDPR export and account-deletion use cases (task 19.1, Req 14.1-14.4).

These worked examples exercise
:class:`edu_it_application.gdpr.ExportUserDataUseCase` and
:class:`edu_it_application.gdpr.DeleteAccountUseCase` against the infrastructure
port fakes — the in-memory personal-data gateway, object storage, session
repository, session store, and the ``Test_Clock`` — exactly as the test/dev
composition root wires them, with no database, Redis, or real clock. The
property-based coverage of export completeness (Property 19) and deletion
completeness and atomicity (Property 20) lives in the infrastructure test
package; these examples pin the use-case orchestration: the export is written to
object storage covering every personal-data table, a confirmed deletion removes
data and revokes every Session, an unconfirmed deletion changes nothing, and a
gateway failure surfaces as :class:`AccountDeletionError`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.gdpr import (
    AccountDeletionError,
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_domain.ports.repository import SessionRecord
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_PersonalDataGateway,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_EXPIRES = _T0 + timedelta(days=14)
_USER = "user-1"


def _clock() -> Test_Clock:
    clock = Test_Clock()
    clock.set(_T0)
    return clock


# --- Export (Req 14.1) -------------------------------------------------------


def test_export_writes_a_document_covering_every_personal_data_table() -> None:
    gateway = In_Memory_PersonalDataGateway()
    gateway.add_row("user", _USER, {"stable_id": _USER, "email": "a@example.test"})
    gateway.add_row("enrollment", _USER, {"track": "t1"})
    storage = In_Memory_ObjectStorage()
    use_case = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)

    result = use_case.execute(ExportUserDataRequest(user_stable_id=_USER))

    stored = storage.get(result.storage_key)
    assert stored.content_type == "application/json"
    document = json.loads(stored.data)
    # Every personal-data table is a key, tables with no rows present as empty.
    assert set(document) == set(PERSONAL_DATA_TABLES)
    assert document["user"] == [{"stable_id": _USER, "email": "a@example.test"}]
    assert document["enrollment"] == [{"track": "t1"}]
    assert document["mastery"] == []  # a table the user has no rows in
    assert result.size_bytes == len(stored.data)


def test_export_for_a_user_with_no_rows_still_covers_every_table() -> None:
    gateway = In_Memory_PersonalDataGateway()
    storage = In_Memory_ObjectStorage()
    use_case = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)

    result = use_case.execute(ExportUserDataRequest(user_stable_id="ghost"))

    document = json.loads(storage.get(result.storage_key).data)
    assert set(document) == set(PERSONAL_DATA_TABLES)
    assert all(rows == [] for rows in document.values())


def test_export_uses_the_injected_key_factory() -> None:
    gateway = In_Memory_PersonalDataGateway()
    storage = In_Memory_ObjectStorage()
    use_case = ExportUserDataUseCase(
        personal_data=gateway,
        object_storage=storage,
        key_factory=lambda stable_id: f"custom/{stable_id}",
    )

    result = use_case.execute(ExportUserDataRequest(user_stable_id=_USER))

    assert result.storage_key == f"custom/{_USER}"
    assert storage.exists(result.storage_key)


# --- Deletion (Req 14.2, 14.3, 14.4) -----------------------------------------


def _delete_use_case(
    gateway: In_Memory_PersonalDataGateway,
    sessions: In_Memory_SessionRepository,
    session_store: In_Memory_SessionStore,
) -> DeleteAccountUseCase:
    return DeleteAccountUseCase(
        personal_data=gateway,
        sessions=sessions,
        session_store=session_store,
        clock=_clock(),
    )


def test_confirmed_deletion_removes_data_and_revokes_all_sessions() -> None:
    gateway = In_Memory_PersonalDataGateway()
    gateway.add_row("user", _USER, {"stable_id": _USER})
    gateway.add_row("enrollment", _USER, {"track": "t1"})
    gateway.add_row("enrollment", _USER, {"track": "t2"})
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(_clock())
    for ref in ("s1", "s2"):
        sessions.add(SessionRecord(server_side_ref=ref, user_stable_id=_USER, expires_at=_EXPIRES))
        session_store.create(ref, _USER, expires_at=_EXPIRES)

    result = _delete_use_case(gateway, sessions, session_store).execute(
        DeleteAccountRequest(user_stable_id=_USER, confirmed=True)
    )

    assert result.deleted is True
    assert result.rows_removed == 3
    assert result.sessions_revoked == 2
    # No personal data remains and no Session authenticates.
    assert gateway.export_user_data(_USER)["enrollment"] == []
    assert session_store.resolve("s1") is None
    assert session_store.resolve("s2") is None


def test_unconfirmed_deletion_changes_nothing() -> None:
    gateway = In_Memory_PersonalDataGateway()
    gateway.add_row("user", _USER, {"stable_id": _USER})
    sessions = In_Memory_SessionRepository()
    sessions.add(SessionRecord(server_side_ref="s1", user_stable_id=_USER, expires_at=_EXPIRES))
    session_store = In_Memory_SessionStore(_clock())
    session_store.create("s1", _USER, expires_at=_EXPIRES)

    result = _delete_use_case(gateway, sessions, session_store).execute(
        DeleteAccountRequest(user_stable_id=_USER, confirmed=False)
    )

    assert result.deleted is False
    assert result.rows_removed == 0
    assert result.sessions_revoked == 0
    # Nothing removed, Session still authenticates.
    assert gateway.row_count("user", _USER) == 1
    assert session_store.resolve("s1") is not None


def test_deletion_failure_raises_and_leaves_tables_unchanged() -> None:
    gateway = In_Memory_PersonalDataGateway(fail_on_table="enrollment")
    gateway.add_row("user", _USER, {"stable_id": _USER})
    gateway.add_row("enrollment", _USER, {"track": "t1"})
    sessions = In_Memory_SessionRepository()
    session_store = In_Memory_SessionStore(_clock())

    with pytest.raises(AccountDeletionError):
        _delete_use_case(gateway, sessions, session_store).execute(
            DeleteAccountRequest(user_stable_id=_USER, confirmed=True)
        )

    # Atomic: every personal-data table is left unchanged on failure (Req 14.4).
    assert gateway.row_count("user", _USER) == 1
    assert gateway.row_count("enrollment", _USER) == 1
