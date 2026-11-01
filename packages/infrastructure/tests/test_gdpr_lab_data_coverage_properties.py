"""GDPR coverage of the lab personal-data tables (hands-on-labs task 15.8).

Feature: hands-on-labs, Property 25: Lab data export and deletion are complete

**Validates: Requirements 24.1, 24.2, 24.5**

Runs the real GDPR use cases over the SQL gateway against in-memory SQLite. The
lab personal-data tables are *derived from the schema metadata* (every ``lab*``
table that reaches ``user`` through foreign keys), not listed by hand, so a
personal-data table added by this spec but missing from the export, deletion or
retention coverage fails the coverage test (Req 24.5).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_application.labs.purge_lab_data import PurgeLabData
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_LabSessionRepository,
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_LabSessionRepository,
    Postgres_PersonalDataGateway,
    metadata,
)
from edu_it_infrastructure.persistence.personal_data import _OWNING_FOREIGN_KEY
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import CursorResult, create_engine, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

T = metadata.tables
_NOW = datetime(2024, 6, 1, tzinfo=UTC)
_OLD = _NOW - config.LAB_RETENTION_PERIOD - timedelta(days=1)


def _pk(result: CursorResult[Any]) -> Any:
    key = result.inserted_primary_key
    assert key is not None
    return key[0]


def _reaches_user(table_name: str) -> bool:
    """Whether ``table_name`` is owned by a User directly or via foreign keys."""
    table = T[table_name]
    if "user_id" in table.c:
        return True
    return any(
        _reaches_user(fk.column.table.name)
        for fk in table.foreign_keys
        if fk.column.table.name not in {table_name, "lab", "lab_task", "lab_check"}
    )


def _lab_personal_tables() -> tuple[str, ...]:
    return tuple(sorted(n for n in T if n.startswith("lab") and n != "user" and _reaches_user(n)))


LAB_TABLES = _lab_personal_tables()


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(eng)
    return eng


def _seed_user(
    engine: Engine, name: str, shape: list[list[int]], *, ended_at: datetime | None
) -> None:
    """Seed a user with sessions; ``shape[i][j]`` is the completions of run j of session i."""
    with engine.begin() as c:
        # The repository joins ``lab``; SQLite does not enforce the lesson/provenance FKs.
        if c.execute(select(T["lab"].c.id)).first() is None:
            c.execute(insert(T["lab"]).values(stable_id="lab1", lesson_id=1, provenance_id=1))
        uid = _pk(
            c.execute(
                insert(T["user"]).values(
                    stable_id=name, email=f"{name}@example.test", locale="en", status="verified"
                )
            )
        )
        for i, runs in enumerate(shape):
            sid = _pk(
                c.execute(
                    insert(T["lab_session"]).values(
                        stable_id=f"ls-{name}-{i}",
                        user_id=uid,
                        lab_id=1,
                        status="ended",
                        started_at=_OLD,
                        ttl_at=_OLD,
                        ended_at=ended_at,
                    )
                )
            )
            for j, completions in enumerate(runs):
                rid = _pk(
                    c.execute(
                        insert(T["lab_run"]).values(
                            stable_id=f"lr-{name}-{i}-{j}",
                            lab_session_id=sid,
                            status="passed",
                            accepted_at=_OLD,
                            deadline_at=_OLD,
                        )
                    )
                )
                for k in range(completions):
                    c.execute(
                        insert(T["lab_task_completion"]).values(
                            lab_run_id=rid, lab_task_id=k + 1, passed=True
                        )
                    )


def _expected(shape: list[list[int]]) -> dict[str, int]:
    return {
        "lab_session": len(shape),
        "lab_run": sum(len(runs) for runs in shape),
        "lab_task_completion": sum(sum(runs) for runs in shape),
    }


def _count(engine: Engine, table: str) -> int:
    with engine.begin() as c:
        return int(c.execute(select(func.count()).select_from(T[table])).scalar_one())


_shapes = st.lists(st.lists(st.integers(0, 3), max_size=3), max_size=3)


@settings(max_examples=100, deadline=None)
@given(mine=_shapes, theirs=_shapes, with_sandbox=st.booleans())
def test_lab_export_and_deletion_are_complete(
    mine: list[list[int]], theirs: list[list[int]], with_sandbox: bool
) -> None:
    """Feature: hands-on-labs, Property 25: Lab data export and deletion are complete."""
    engine = _engine()
    gateway = Postgres_PersonalDataGateway(engine)
    _seed_user(engine, "gone", mine, ended_at=_OLD)
    _seed_user(engine, "kept", theirs, ended_at=_OLD)

    # Req 24.1: the export holds every row of the learner and none of another's.
    storage = In_Memory_ObjectStorage()
    result = ExportUserDataUseCase(personal_data=gateway, object_storage=storage).execute(
        ExportUserDataRequest(user_stable_id="gone")
    )
    document = json.loads(storage.get(result.storage_key).data)
    expected = _expected(mine)
    for table in LAB_TABLES:
        assert len(document[table]) == expected[table], table
    assert all(r["stable_id"].startswith("ls-gone-") for r in document["lab_session"])
    assert all(r["stable_id"].startswith("lr-gone-") for r in document["lab_run"])
    assert not [key for key in document if "terminal" in key]

    # Req 24.2: deletion removes all of them, spares others, destroys the Sandbox.
    clock = Test_Clock(start=_NOW)
    sandbox = Fake_SandboxProvider(clock)
    lab_sessions = In_Memory_LabSessionRepository()
    handle = None
    if with_sandbox:
        lab = LabDefinition(
            lab_id="lab1",
            kind="terminal",
            image="img",
            concept_ids=("cn1",),
            tasks=(LabTaskDefinition("t1", required=True),),
            checks=(),
            bundle_path="lab1",
        )
        handle = sandbox.provision(build_sandbox_spec(lab, "active"))
        lab_sessions.add_if_no_active(
            LabSession(
                "active",
                "gone",
                "lab1",
                SessionStatus.READY,
                _NOW,
                _NOW + timedelta(hours=1),
                sandbox_ref=handle.sandbox_id,
            )
        )
    outcome = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        clock=clock,
        lab_sessions=lab_sessions,
        sandbox=sandbox,
    ).execute(DeleteAccountRequest(user_stable_id="gone", confirmed=True))

    assert outcome.deleted
    gone = gateway.export_user_data("gone")
    kept = gateway.export_user_data("kept")
    kept_expected = _expected(theirs)
    for table in LAB_TABLES:
        assert gone[table] == [], table
        assert len(kept[table]) == kept_expected[table], table
    if handle is not None:
        assert handle not in sandbox.list_sandboxes()


# --- Coverage test (Req 24.4, 24.5) ------------------------------------------


def test_every_lab_personal_data_table_is_in_export_deletion_and_retention() -> None:
    # The schema-derived set is non-empty and is what this spec introduced.
    assert {"lab_session", "lab_run", "lab_task_completion"} <= set(LAB_TABLES)

    # Export + deletion scope: listed and resolvable to the User by the SQL gateway.
    for table in LAB_TABLES:
        assert table in PERSONAL_DATA_TABLES, f"{table} missing from PERSONAL_DATA_TABLES"
        assert "user_id" in T[table].c or table in _OWNING_FOREIGN_KEY, (
            f"{table} has no ownership path for export/deletion"
        )

    # Export and deletion behave for each table (a table the gateway omits fails here).
    engine = _engine()
    gateway = Postgres_PersonalDataGateway(engine)
    shape = [[1, 2], [3]]
    _seed_user(engine, "u1", shape, ended_at=_OLD)
    assert set(LAB_TABLES) <= set(gateway.export_user_data("u1"))
    for table, count in _expected(shape).items():
        assert len(gateway.export_user_data("u1")[table]) == count
    assert set(_expected(shape)) == set(LAB_TABLES), (
        "seed helper does not cover a new lab personal-data table; extend it and the purge"
    )

    # Retention: the purge job leaves no row of any lab personal-data table behind.
    clock = Test_Clock(start=_NOW)
    purged = PurgeLabData(sessions=Postgres_LabSessionRepository(engine), clock=clock).execute()
    assert purged == len(shape)
    for table in LAB_TABLES:
        assert _count(engine, table) == 0, f"{table} not covered by retention"
