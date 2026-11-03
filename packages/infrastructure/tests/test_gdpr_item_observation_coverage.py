"""GDPR coverage of ``item_observation`` (ai-content task 19.2, Req 21.4, 21.6).

``item_observation`` holds a learner reference, so it is registered in
``PERSONAL_DATA_TABLES`` and `docs/DATA-MODEL.md` section 13, exported, deleted with the
learner and covered by a retention period. Runs the real GDPR use cases over the SQL gateway
against in-memory SQLite. The aggregate ``item_statistic`` keeps no learner link and survives
the deletion.

Validates: Requirements 21.4, 21.6
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_domain import config
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import NewObservation
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_ItemQualityRepository,
    Postgres_PersonalDataGateway,
    metadata,
)
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_DOC = Path(__file__).resolve().parents[3] / "docs" / "DATA-MODEL.md"
_ITEM = ItemKey(ItemKind.QUESTION, "q-1")


def _engine() -> Engine:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(eng)
    return eng


def _user(engine: Engine, name: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(metadata.tables["user"]).values(
                stable_id=name, email=f"{name}@example.test", locale="en", status="verified"
            )
        )


def _use_cases(
    engine: Engine,
) -> tuple[ExportUserDataUseCase, DeleteAccountUseCase, In_Memory_ObjectStorage]:
    gateway = Postgres_PersonalDataGateway(engine)
    storage = In_Memory_ObjectStorage()
    clock = Test_Clock(_T0)
    export = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)
    delete = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        clock=clock,
    )
    return export, delete, storage


def _seed(engine: Engine) -> Postgres_ItemQualityRepository:
    repo = Postgres_ItemQualityRepository(engine, Test_Clock(_T0))
    for name in ("u1", "u2"):
        _user(engine, name)
        repo.record(NewObservation(name, _ITEM, True, _T0, 1200))
        repo.record(NewObservation(name, _ITEM, False, _T0))
    return repo


def test_gdpr_item_observation_coverage_is_registered_before_user() -> None:
    assert "item_observation" in PERSONAL_DATA_TABLES
    assert PERSONAL_DATA_TABLES.index("item_observation") < PERSONAL_DATA_TABLES.index("user")
    assert "user_id" in metadata.tables["item_observation"].c


def test_gdpr_item_observation_coverage_is_listed_in_data_model_section_13() -> None:
    text = _DOC.read_text(encoding="utf-8")
    section = text.split("## 13. Personal Data", 1)[1].split("\n## 14.", 1)[0]
    row = next(line for line in section.splitlines() if line.startswith("| `item_observation`"))
    assert "Yes" in row and row.count("✓") == 3
    assert "AI_RETENTION_OBSERVATION" in row
    assert re.search(r"`ai_cursor`", section)  # the non-personal sibling is listed as such


def test_gdpr_item_observation_coverage_has_a_positive_retention_period() -> None:
    assert timedelta(0) < config.AI_RETENTION_OBSERVATION
    assert "AI_RETENTION_OBSERVATION" in config.AI_CONFIG_KEYS
    config.validate_ai_config(config)


def test_gdpr_item_observation_coverage_export_includes_only_the_subjects_rows() -> None:
    engine = _engine()
    _seed(engine)
    export, _, storage = _use_cases(engine)

    result = export.execute(ExportUserDataRequest(user_stable_id="u1"))
    document = json.loads(storage.get(result.storage_key).data)
    rows = document["item_observation"]
    assert len(rows) == 2
    assert all("id" not in row for row in rows)  # no internal key leaves the system
    assert {row["item_stable_id"] for row in rows} == {"q-1"}
    assert sorted(row["correct"] for row in rows) == [False, True]


def test_gdpr_item_observation_coverage_deletion_removes_rows_and_keeps_aggregates() -> None:
    engine = _engine()
    repo = _seed(engine)
    from edu_it_domain.ai.quality import ItemStat

    repo.apply({_ITEM: ItemStat(4, 2, 2, 2400)}, 4)
    export, delete, storage = _use_cases(engine)

    delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))

    table = metadata.tables["item_observation"]
    with engine.connect() as connection:
        remaining = len(connection.execute(select(table.c.user_id)).all())
    assert remaining == 2  # u2 untouched
    after = json.loads(
        storage.get(export.execute(ExportUserDataRequest(user_stable_id="u1")).storage_key).data
    )
    assert after["item_observation"] == []
    # The aggregate has no learner link and is unchanged.
    assert repo.get(_ITEM) == ItemStat(4, 2, 2, 2400)
    assert "user_id" not in metadata.tables["item_statistic"].c
