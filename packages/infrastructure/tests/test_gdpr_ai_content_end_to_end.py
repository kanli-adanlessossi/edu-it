"""End-to-end GDPR flow of the ai-content personal data (task 23.2, Req 21.1 to 21.6).

One learner owns problem reports, recommendations (one dismissed), advisor audit entries and
item observations. The real export and deletion use cases and ``PurgeAiData`` run over the SQL
gateway against in-memory SQLite: the export holds every row, deletion removes them, a second
export holds none, and the ``item_statistic`` aggregate is unchanged and has no learner link.
Registration tests fail when any personal-data table of this spec is missing from
``PERSONAL_DATA_TABLES``, section 13 of `docs/DATA-MODEL.md`, the export, the deletion or the
retention purge.

Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5, 21.6
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_application.ai import PurgeAiData
from edu_it_application.gdpr import (
    DeleteAccountRequest,
    DeleteAccountUseCase,
    ExportUserDataRequest,
    ExportUserDataUseCase,
)
from edu_it_domain import config
from edu_it_domain.ai.quality import ItemStat
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import NewObservation
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES
from edu_it_domain.ports.problem_reports import ProblemReport
from edu_it_infrastructure.fakes import (
    In_Memory_ObjectStorage,
    In_Memory_SessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)
from edu_it_infrastructure.persistence import (
    Postgres_ItemQualityRepository,
    Postgres_PathRecommendationRepository,
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
_STAT = ItemStat(4, 2, 2, 2400)

#: Every personal-data table this spec introduces, with its retention key.
_AI_PERSONAL_TABLES = {
    "item_observation": "AI_RETENTION_OBSERVATION",
    "problem_report": "AI_RETENTION_PROBLEM_REPORT",
    "path_recommendation": "AI_RETENTION_RECOMMENDATION",
    "advisor_audit_log": "AI_RETENTION_AUDIT",
}
#: Tables of this spec that are aggregate or operational and must hold no learner link.
_NON_PERSONAL_TABLES = ("item_statistic", "item_flag", "variant", "ai_cursor", "budget_ledger")


class _World:
    def __init__(self) -> None:
        self.engine: Engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        metadata.create_all(self.engine)
        self.clock = Test_Clock(_T0)
        self.quality = Postgres_ItemQualityRepository(self.engine, self.clock)
        self.reports = Postgres_ProblemReportRepository(self.engine)
        self.recs = Postgres_PathRecommendationRepository(self.engine)
        gateway = Postgres_PersonalDataGateway(self.engine)
        self.storage = In_Memory_ObjectStorage()
        self.export = ExportUserDataUseCase(personal_data=gateway, object_storage=self.storage)
        self.delete = DeleteAccountUseCase(
            personal_data=gateway,
            sessions=In_Memory_SessionRepository(),
            session_store=In_Memory_SessionStore(self.clock),
            clock=self.clock,
        )
        for name in ("u1", "u2"):
            self._seed(name)
        self.quality.apply({_ITEM: _STAT}, 4)

    def _seed(self, name: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(metadata.tables["user"]).values(
                    stable_id=name, email=f"{name}@example.test", locale="en", status="verified"
                )
            )
        self.quality.record(NewObservation(name, _ITEM, True, _T0, 1200))
        self.quality.record(NewObservation(name, _ITEM, False, _T0))
        self.reports.add(
            ProblemReport(f"pr-{name}", name, _ITEM, "v1", "incorrect", _T0, f"note {name}")
        )
        self.recs.record_run(
            PathRecommendation(f"rec-{name}", name, "llm", ("c1", "c2"), f"run-{name}", _T0),
            AdvisorAuditEntry(
                f"aud-{name}", name, f"run-{name}", f"h_{name}", "llm", "accepted", _T0
            ),
        )
        self.recs.dismiss(f"rec-{name}", name, _T0 + timedelta(hours=1))

    def export_of(self, user: str) -> dict[str, list[dict[str, object]]]:
        result = self.export.execute(ExportUserDataRequest(user_stable_id=user))
        document: dict[str, list[dict[str, object]]] = json.loads(
            self.storage.get(result.storage_key).data
        )
        return document


def test_export_deletion_and_second_export_cover_every_ai_personal_table() -> None:
    world = _World()

    first = world.export_of("u1")
    assert len(first["item_observation"]) == 2
    assert [r["stable_id"] for r in first["problem_report"]] == ["pr-u1"]
    assert [r["stable_id"] for r in first["path_recommendation"]] == ["rec-u1"]
    assert first["path_recommendation"][0]["dismissed_at"] is not None  # dismissal exported
    assert [r["stable_id"] for r in first["advisor_audit_log"]] == ["aud-u1"]
    assert all("id" not in row for table in _AI_PERSONAL_TABLES for row in first[table])

    world.delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))

    second = world.export_of("u1")
    for table in _AI_PERSONAL_TABLES:
        assert second[table] == [], table
    # The other learner is untouched.
    other = world.export_of("u2")
    assert len(other["item_observation"]) == 2
    assert len(other["problem_report"]) == 1
    assert len(other["path_recommendation"]) == 1
    assert len(other["advisor_audit_log"]) == 1


def test_item_statistic_is_unchanged_by_deletion_and_has_no_learner_link() -> None:
    world = _World()

    world.delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))

    assert world.quality.get(_ITEM) == _STAT
    assert "item_statistic" not in PERSONAL_DATA_TABLES
    for table in _NON_PERSONAL_TABLES:
        assert "user_id" not in metadata.tables[table].c, table


def test_retention_purge_removes_every_ai_personal_table_and_is_idempotent() -> None:
    world = _World()
    # A later recommendation keeps the old one from being the learner's newest (kept) row.
    later = _T0 + timedelta(days=1)
    world.recs.record_run(
        PathRecommendation("rec-u1-new", "u1", "rule", ("c1",), "run-new", later),
        AdvisorAuditEntry("aud-u1-new", "u1", "run-new", "h_new", "rule", "fallback", later),
    )
    purge = PurgeAiData(
        quality=world.quality,
        reports=world.reports,
        recommendations=world.recs,
        clock=world.clock,
    )
    world.clock.set(_T0 + max(getattr(config, key) for key in _AI_PERSONAL_TABLES.values()))
    world.clock.advance(timedelta(days=2))

    result = purge.execute()

    assert result.observations == 4
    assert result.problem_reports == 2
    assert result.audit_entries == 3
    assert result.recommendations == 1  # u1's superseded one; each learner's newest is kept
    assert purge.execute().total == 0
    assert world.quality.get(_ITEM) == _STAT


@pytest.mark.parametrize("table", sorted(_AI_PERSONAL_TABLES))
def test_every_ai_personal_table_is_registered_in_scope_doc_and_retention(table: str) -> None:
    assert table in PERSONAL_DATA_TABLES  # export and deletion iterate this tuple
    assert PERSONAL_DATA_TABLES.index(table) < PERSONAL_DATA_TABLES.index("user")
    assert "user_id" in metadata.tables[table].c

    text = _DOC.read_text(encoding="utf-8")
    section = text.split("## 13. Personal Data", 1)[1].split("\n## 14.", 1)[0]
    row = next(line for line in section.splitlines() if line.startswith(f"| `{table}`"))
    assert "Yes" in row and row.count("✓") == 3

    key = _AI_PERSONAL_TABLES[table]
    assert key in row
    assert key in config.AI_CONFIG_KEYS
    assert timedelta(0) < getattr(config, key)
