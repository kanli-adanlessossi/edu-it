"""Tests for the ``ai.retain`` job of the AI worker (ai-content task 23.1).

Run against the in-memory ports; no model, no network.

**Validates: Requirements 21.4**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.config import AI_RETENTION_AUDIT, AI_RETENTION_INTERVAL
from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    Fake_SandboxProvider,
    In_Memory_CatalogRepository,
    In_Memory_GenerationLedger,
    In_Memory_ItemFlagRepository,
    In_Memory_ItemQualityRepository,
    In_Memory_PathRecommendationRepository,
    In_Memory_ProblemReportRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker.ai.composition import RetentionPorts, build_ai_worker
from edu_it_worker.ai.retention_jobs import RETAIN_JOB

T0 = datetime(2025, 1, 1, tzinfo=UTC)
USER = "usr_0123456789abcdef"


class _Parents:
    def get(self, stable_id: str) -> None:
        return None

    def approved_ids(self) -> list[str]:
        return []


def _world() -> tuple[
    object, Test_Clock, In_Memory_TaskQueue, In_Memory_PathRecommendationRepository
]:
    clock = Test_Clock(T0)
    queue = In_Memory_TaskQueue()
    variants = In_Memory_VariantRepository(clock)
    recs = In_Memory_PathRecommendationRepository()
    provider = Fake_LLMProvider({})
    worker = build_ai_worker(
        queue=queue,
        clock=clock,
        ids=Sequential_IdGenerator("id"),
        ledger=In_Memory_GenerationLedger(clock),
        parents=_Parents(),
        variants=variants,
        review_tasks=In_Memory_ReviewTaskRepository(clock, variants),
        flags=In_Memory_ItemFlagRepository(),
        catalog=In_Memory_CatalogRepository(),
        sandbox=Fake_SandboxProvider(clock),
        retention_ports=RetentionPorts(
            quality=In_Memory_ItemQualityRepository(),
            reports=In_Memory_ProblemReportRepository(),
            recommendations=recs,
        ),
        settings=CompositionSettings(),
        wire=lambda _scope: (provider, provider),
    )
    return worker, clock, queue, recs


def test_retention_job_is_scheduled_once_per_bucket_with_a_bucket_dedupe_key() -> None:
    worker, _clock, queue, _recs = _world()
    assert RETAIN_JOB in worker.consumers  # type: ignore[attr-defined]
    assert RETAIN_JOB in worker.scheduler.tick()  # type: ignore[attr-defined]
    assert worker.scheduler.tick() == []  # type: ignore[attr-defined]
    (job,) = queue.pending_for(RETAIN_JOB)
    assert job.dedupe_key is not None
    assert job.dedupe_key.startswith("ai.retain:tick:")
    assert timedelta(0) < AI_RETENTION_INTERVAL


def test_retention_handler_purges_expired_audit_rows_and_is_idempotent() -> None:
    worker, clock, _queue, recs = _world()
    old = T0 - AI_RETENTION_AUDIT - timedelta(days=1)
    recs.record_run(
        PathRecommendation("rec-1", USER, "llm", ("c1",), "run-1", old),
        AdvisorAuditEntry("aud-1", USER, "run-1", "h", "llm", "accepted", old),
    )
    handler = worker.consumers[RETAIN_JOB]  # type: ignore[attr-defined]
    handler({})
    assert recs.audit_entries(USER) == ()
    assert recs.get("rec-1") is not None  # the learner's newest recommendation stays
    handler({})
    assert recs.get("rec-1") is not None
    assert clock.now() == T0
