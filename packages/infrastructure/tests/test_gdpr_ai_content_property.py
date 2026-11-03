"""Property 38: export, deletion and retention cover every new personal-data table.

For any learner with a random number of reports, recommendations (some dismissed), audit entries
and observations, the export holds them all; after deletion a second export holds none of that
learner's rows, the other learner and the ``item_statistic`` aggregate are unchanged; and
``PurgeAiData`` deletes exactly the rows past retention and is idempotent.

**Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5, 21.6**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

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
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, tzinfo=UTC)
_ITEM = ItemKey(ItemKind.QUESTION, "q-1")
_STAT = ItemStat(4, 2, 2, 2400)
_TABLES = (
    "item_observation",
    "problem_report",
    "path_recommendation",
    "advisor_audit_log",
)
_RETENTION_KEYS = (
    "AI_RETENTION_OBSERVATION",
    "AI_RETENTION_PROBLEM_REPORT",
    "AI_RETENTION_AUDIT",
)


@settings(max_examples=25, deadline=None)
@given(
    observations=st.integers(0, 4),
    reports=st.integers(0, 3),
    recommendations=st.integers(0, 3),
    dismissed=st.integers(0, 3),
)
def test_export_deletion_and_retention_cover_every_ai_personal_table(
    observations: int, reports: int, recommendations: int, dismissed: int
) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(engine)
    clock = Test_Clock(_T0)
    quality = Postgres_ItemQualityRepository(engine, clock)
    report_repo = Postgres_ProblemReportRepository(engine)
    recs = Postgres_PathRecommendationRepository(engine)
    gateway = Postgres_PersonalDataGateway(engine)
    storage = In_Memory_ObjectStorage()
    export = ExportUserDataUseCase(personal_data=gateway, object_storage=storage)
    delete = DeleteAccountUseCase(
        personal_data=gateway,
        sessions=In_Memory_SessionRepository(),
        session_store=In_Memory_SessionStore(clock),
        clock=clock,
    )

    def export_of(user: str) -> dict[str, list[dict[str, object]]]:
        result = export.execute(ExportUserDataRequest(user_stable_id=user))
        document: dict[str, list[dict[str, object]]] = json.loads(
            storage.get(result.storage_key).data
        )
        return document

    for name in ("u1", "u2"):
        with engine.begin() as connection:
            connection.execute(
                insert(metadata.tables["user"]).values(
                    stable_id=name, email=f"{name}@example.test", locale="en", status="verified"
                )
            )
        for i in range(observations):
            quality.record(NewObservation(name, _ITEM, i % 2 == 0, _T0, 1000 + i))
        for i in range(reports):
            report_repo.add(
                ProblemReport(f"pr-{name}-{i}", name, _ITEM, "v1", "incorrect", _T0, "note")
            )
        for i in range(recommendations):
            recs.record_run(
                PathRecommendation(f"rec-{name}-{i}", name, "llm", ("c1",), f"run-{name}-{i}", _T0),
                AdvisorAuditEntry(
                    f"aud-{name}-{i}", name, f"run-{name}-{i}", f"h{i}", "llm", "accepted", _T0
                ),
            )
            if i < dismissed:
                recs.dismiss(f"rec-{name}-{i}", name, _T0 + timedelta(hours=1))
    quality.apply({_ITEM: _STAT}, 4)

    expected = {
        "item_observation": observations,
        "problem_report": reports,
        "path_recommendation": recommendations,
        "advisor_audit_log": recommendations,
    }
    first = export_of("u1")
    for table in _TABLES:
        assert len(first[table]) == expected[table], table
    assert sum(r["dismissed_at"] is not None for r in first["path_recommendation"]) == min(
        dismissed, recommendations
    )

    delete.execute(DeleteAccountRequest(user_stable_id="u1", confirmed=True))
    second = export_of("u1")
    for table in _TABLES:
        assert second[table] == [], table
    other = export_of("u2")
    for table in _TABLES:
        assert len(other[table]) == expected[table], table
    assert quality.get(_ITEM) == _STAT

    purge = PurgeAiData(quality=quality, reports=report_repo, recommendations=recs, clock=clock)
    clock.set(_T0 + max(getattr(config, key) for key in _RETENTION_KEYS))
    clock.advance(timedelta(days=2))
    result = purge.execute()
    assert result.observations == observations
    assert result.problem_reports == reports
    assert result.audit_entries == recommendations
    assert purge.execute().total == 0
    for table in ("item_observation", "problem_report", "advisor_audit_log"):
        assert export_of("u2")[table] == [], table
    assert quality.get(_ITEM) == _STAT
