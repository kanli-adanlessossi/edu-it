"""Unit tests for PurgeAiData (task 23.1; Req 21.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_application.ai import PurgeAiData
from edu_it_domain import config
from edu_it_domain.ai.quality import ItemStat
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.item_quality import NewObservation
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_domain.ports.problem_reports import ProblemReport
from edu_it_infrastructure.fakes import (
    In_Memory_ItemQualityRepository,
    In_Memory_PathRecommendationRepository,
    In_Memory_ProblemReportRepository,
    Test_Clock,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)
USER = "usr_a"
KEY = ItemKey(ItemKind.QUESTION, "q1")


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock(NOW)
        self.quality = In_Memory_ItemQualityRepository()
        self.reports = In_Memory_ProblemReportRepository()
        self.recs = In_Memory_PathRecommendationRepository()
        self.purge = PurgeAiData(
            quality=self.quality, reports=self.reports, recommendations=self.recs, clock=self.clock
        )

    def seed(self, label: str, age: timedelta, retention: timedelta) -> None:
        """One row per table, ``age`` older than ``retention`` (positive age = expired)."""
        at = NOW - retention - age
        self.quality.record(NewObservation(USER, KEY, True, at))
        self.reports.add(ProblemReport(f"pr-{label}", USER, KEY, "v1", "unclear", at))
        self.recs.record_run(
            PathRecommendation(f"rec-{label}", USER, "llm", ("c1",), f"run-{label}", at),
            AdvisorAuditEntry(f"aud-{label}", USER, f"run-{label}", "h", "llm", "accepted", at),
        )


def test_deletes_expired_rows_in_every_table_and_keeps_current_ones() -> None:
    env = _Env()
    env.seed("old", timedelta(days=1), config.AI_RETENTION_OBSERVATION)
    env.seed("new", timedelta(days=-1), config.AI_RETENTION_OBSERVATION)
    # Retention periods differ per table: re-seed with each table's own period.
    env.reports.add(
        ProblemReport(
            "pr-year",
            USER,
            KEY,
            "v1",
            "unclear",
            NOW - config.AI_RETENTION_PROBLEM_REPORT + timedelta(days=1),
        )
    )
    env.quality.apply({KEY: ItemStat(1, 1, 0, 0)}, 1)

    result = env.purge.execute()

    # "old" is expired in the 90-day tables; the 365-day report is untouched.
    assert result.observations == 1
    assert result.audit_entries == 1
    assert result.problem_reports == 0
    assert env.reports.get("pr-year") is not None
    assert len(env.quality.observations_after(0, 10)) == 1
    assert [a.stable_id for a in env.recs.audit_entries(USER)] == ["aud-new"]
    # The aggregate is untouched.
    assert env.quality.get(KEY) == ItemStat(1, 1, 0, 0)


def test_a_learners_newest_recommendation_is_kept_even_when_old() -> None:
    env = _Env()
    env.seed("only", timedelta(days=400), config.AI_RETENTION_RECOMMENDATION)
    assert env.purge.execute().recommendations == 0
    assert env.recs.get("rec-only") is not None
    env.seed("later", timedelta(days=-1), config.AI_RETENTION_RECOMMENDATION)
    assert env.purge.execute().recommendations == 1
    assert env.recs.get("rec-only") is None


def test_expired_reports_are_deleted_after_their_own_period() -> None:
    env = _Env()
    env.seed("r", timedelta(days=1), config.AI_RETENTION_PROBLEM_REPORT)
    assert env.purge.execute().problem_reports == 1
    assert env.reports.get("pr-r") is None


def test_second_run_at_the_same_instant_deletes_nothing() -> None:
    env = _Env()
    env.seed("old", timedelta(days=500), config.AI_RETENTION_PROBLEM_REPORT)
    env.seed("new", timedelta(days=-1), config.AI_RETENTION_OBSERVATION)
    assert env.purge.execute().total > 0
    assert env.purge.execute().total == 0
