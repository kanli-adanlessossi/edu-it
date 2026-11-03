"""GDPR coverage of ``problem_report`` (ai-content task 20.1, Req 21.1, 21.2, 21.4, 21.6).

``problem_report`` holds a learner reference and a free-text comment, so it is registered in
``PERSONAL_DATA_TABLES`` and `docs/DATA-MODEL.md` section 13, exported, deleted with the
learner and covered by a retention period. Runs the real GDPR use cases over the SQL gateway
against in-memory SQLite.

Validates: Requirements 21.1, 21.2, 21.4, 21.6
"""

from __future__ import annotations

import json
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
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_domain.ports.problem_reports import ProblemReport
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_PersonalDataGateway,
    Postgres_ProblemReportRepository,
    metadata,
)
from sqlalchemy import create_engine, insert
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


def _seed(engine: Engine) -> Postgres_ProblemReportRepository:
    repo = Postgres_ProblemReportRepository(engine)
    for name in ("u1", "u2"):
        _user(engine, name)
        repo.add(ProblemReport(f"pr-{name}", name, _ITEM, "v1", "incorrect", _T0, f"note {name}"))
    return repo


def test_gdpr_problem_report_coverage_is_registered_before_user() -> None:
    assert "problem_report" in PERSONAL_DATA_TABLES
    assert PERSONAL_DATA_TABLES.index("problem_report") < PERSONAL_DATA_TABLES.index("user")
    assert "user_id" in metadata.tables["problem_report"].c


def test_gdpr_problem_report_coverage_is_listed_in_data_model_section_13() -> None:
    text = _DOC.read_text(encoding="utf-8")
    section = text.split("## 13. Personal Data", 1)[1].split("\n## 14.", 1)[0]
    row = next(line for line in section.splitlines() if line.startswith("| `problem_report`"))
    assert "Yes" in row and row.count("✓") == 3
    assert "AI_RETENTION_PROBLEM_REPORT" in row


def test_gdpr_problem_report_coverage_has_a_positive_retention_period() -> None:
    assert timedelta(0) < config.AI_RETENTION_PROBLEM_REPORT
    assert "AI_RETENTION_PROBLEM_REPORT" in config.AI_CONFIG_KEYS
    config.validate_ai_config(config)


def test_gdpr_problem_report_coverage_export_includes_only_the_subjects_reports() -> None:
    engine = _engine()
    _seed(engine)
    export, _, storage = _use_cases(engine)

    result = export.execute(ExportUserDataRequest(user_stable_id="u1"))
    rows = json.loads(storage.get(result.storage_key).data)["problem_report"]
    assert len(rows) == 1
    assert "id" not in rows[0]  # no internal key leaves the system
    assert rows[0]["stable_id"] == "pr-u1"
    assert rows[0]["comment"] == "note u1"
    assert rows[0]["content_version"] == "v1"


def test_gdpr_problem_report_coverage_deletion_removes_the_subjects_reports() -> None:
    engine = _engine()
    repo = _seed(engine)
    export, delete, storage = _use_cases(engine)

    delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))

    assert repo.get("pr-u1") is None
    assert repo.get("pr-u2") is not None  # u2 untouched
    after = json.loads(
        storage.get(export.execute(ExportUserDataRequest(user_stable_id="u1")).storage_key).data
    )
    assert after["problem_report"] == []
