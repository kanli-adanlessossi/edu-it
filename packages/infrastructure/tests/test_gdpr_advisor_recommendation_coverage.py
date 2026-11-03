"""GDPR coverage of ``path_recommendation`` and ``advisor_audit_log`` (ai-content task 22.2).

Both tables hold a learner reference, so both are registered in ``PERSONAL_DATA_TABLES`` and
`docs/DATA-MODEL.md` section 13, exported (dismissals included), deleted with the learner and
covered by a retention period. Runs the real GDPR use cases over the SQL gateway against
in-memory SQLite.

Validates: Requirements 17.8, 21.1, 21.2, 21.4, 21.6
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_domain import config
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_PathRecommendationRepository,
    Postgres_PersonalDataGateway,
    metadata,
)
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_DOC = Path(__file__).resolve().parents[3] / "docs" / "DATA-MODEL.md"
_RETENTION = {
    "path_recommendation": "AI_RETENTION_RECOMMENDATION",
    "advisor_audit_log": "AI_RETENTION_AUDIT",
}
_TABLES = tuple(_RETENTION)


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


def _seed(engine: Engine) -> Postgres_PathRecommendationRepository:
    repo = Postgres_PathRecommendationRepository(engine)
    for name in ("u1", "u2"):
        _user(engine, name)
        repo.record_run(
            PathRecommendation(f"rec-{name}", name, "llm", ("c1", "c2"), f"run-{name}", _T0),
            AdvisorAuditEntry(
                f"aud-{name}", name, f"run-{name}", f"h_{name}", "llm", "accepted", _T0
            ),
        )
        repo.dismiss(f"rec-{name}", name, _T0 + timedelta(hours=1))
    return repo


@pytest.mark.parametrize("table", _TABLES)
def test_gdpr_advisor_recommendation_coverage_is_registered_before_user(table: str) -> None:
    assert table in PERSONAL_DATA_TABLES
    assert PERSONAL_DATA_TABLES.index(table) < PERSONAL_DATA_TABLES.index("user")
    assert "user_id" in metadata.tables[table].c


@pytest.mark.parametrize("table", _TABLES)
def test_gdpr_advisor_recommendation_coverage_is_listed_in_data_model_section_13(
    table: str,
) -> None:
    text = _DOC.read_text(encoding="utf-8")
    section = text.split("## 13. Personal Data", 1)[1].split("\n## 14.", 1)[0]
    row = next(line for line in section.splitlines() if line.startswith(f"| `{table}`"))
    assert "Yes" in row and row.count("✓") == 3
    assert _RETENTION[table] in row


@pytest.mark.parametrize("table", _TABLES)
def test_gdpr_advisor_recommendation_coverage_has_a_positive_retention_period(
    table: str,
) -> None:
    key = _RETENTION[table]
    assert timedelta(0) < getattr(config, key)
    assert key in config.AI_CONFIG_KEYS
    config.validate_ai_config(config)


def test_gdpr_advisor_recommendation_coverage_export_includes_only_the_subjects_rows() -> None:
    engine = _engine()
    _seed(engine)
    export, _, storage = _use_cases(engine)

    document = json.loads(
        storage.get(export.execute(ExportUserDataRequest(user_stable_id="u1")).storage_key).data
    )
    recommendations = document["path_recommendation"]
    audit = document["advisor_audit_log"]
    assert [r["stable_id"] for r in recommendations] == ["rec-u1"]
    assert [a["stable_id"] for a in audit] == ["aud-u1"]
    assert "id" not in recommendations[0] and "id" not in audit[0]  # no internal key leaves
    assert recommendations[0]["dismissed_at"] is not None  # the dismissal is exported
    assert json.loads(recommendations[0]["ordering"]) == ["c1", "c2"]
    assert audit[0]["handle"] == "h_u1"


def test_gdpr_advisor_recommendation_coverage_deletion_removes_the_subjects_rows() -> None:
    engine = _engine()
    repo = _seed(engine)
    export, delete, storage = _use_cases(engine)

    delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))

    assert repo.get("rec-u1") is None
    assert repo.audit_entries("u1") == ()
    assert repo.get("rec-u2") is not None  # u2 untouched
    assert len(repo.audit_entries("u2")) == 1
    after = json.loads(
        storage.get(export.execute(ExportUserDataRequest(user_stable_id="u1")).storage_key).data
    )
    assert after["path_recommendation"] == []
    assert after["advisor_audit_log"] == []
