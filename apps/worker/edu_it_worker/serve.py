"""Worker entrypoint: real lab and AI consumers plus the health/readiness surface.

Run with ``python -m edu_it_worker.serve``. :func:`build_served_worker` composes the lab
worker (:func:`~edu_it_worker.labs.composition.build_lab_worker`) over the real adapters:
PostgreSQL lab repositories, the Redis task queue, the approved lab content and the
sandbox provider selected by ``SANDBOX_PROVIDER`` (``docker`` is development only and is
refused with ``EDU_IT_ENV=production``). The lab Config is validated before any provider
call, so an invalid Config exits without creating a Sandbox.

The AI worker (:func:`~edu_it_worker.ai.composition.build_ai_worker`) is composed beside it
over the same queue and clock: the variant refill consumers, the approved-only
``ServableCatalog`` and the Approved-question adapter over the synced content. The AI Config
is validated at start, so an invalid one exits before any job runs.

Environment: ``EDU_IT_CONTENT_ROOT`` (default ``content``) is the authored content root and
``EDU_IT_BUNDLE_ROOT`` (default: current directory) is the repository root that the lab
``setup_script`` / ``check_bundle`` paths are relative to.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from edu_it_content.lab_runtime import load_synced_store, setup_script_resolver
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.adapters.parent_questions import Content_ParentQuestionSource
from edu_it_infrastructure.composition import (
    CompositionSettings,
    build_generation_ledger,
    build_sandbox_provider,
)
from edu_it_infrastructure.lab_composition import build_lab_ports
from edu_it_infrastructure.persistence.advisor_targets import Postgres_AdvisorTargets
from edu_it_infrastructure.persistence.item_flags import Postgres_ItemFlagRepository
from edu_it_infrastructure.persistence.item_quality import Postgres_ItemQualityRepository
from edu_it_infrastructure.persistence.learning_repositories import (
    Postgres_CatalogRepository,
    Postgres_LessonRepository,
    Postgres_MasteryRepository,
    Postgres_ReviewRepository,
)
from edu_it_infrastructure.persistence.path_recommendations import (
    Postgres_PathRecommendationRepository,
)
from edu_it_infrastructure.persistence.problem_reports import Postgres_ProblemReportRepository
from edu_it_infrastructure.persistence.variants import (
    Postgres_ReviewTaskRepository,
    Postgres_VariantRepository,
)

from edu_it_worker.ai.composition import (
    AdvisorPorts,
    AiWorker,
    QualityPorts,
    RetentionPorts,
    build_ai_worker,
)
from edu_it_worker.app import create_health_app
from edu_it_worker.labs import LabWorker, build_lab_worker

__all__ = ["ServedWorkers", "build_served_worker", "main"]


@dataclass(frozen=True, slots=True)
class ServedWorkers:
    """The lab worker and the AI worker that one process runs."""

    lab: LabWorker
    ai: AiWorker

    def start(self) -> None:
        """Start both workers; if the AI worker fails to start the lab worker is stopped."""
        self.lab.start()
        try:
            self.ai.start()
        except Exception:
            self.lab.stop()
            raise

    def stop(self) -> None:
        """Stop both workers; a failing AI stop never skips the lab worker."""
        try:
            self.ai.stop()
        finally:
            self.lab.stop()


def build_served_worker(
    *, settings: CompositionSettings | None = None, poll_interval_s: float = 1.0
) -> ServedWorkers:
    """Compose the lab and AI workers over the real adapters (nothing is started)."""
    settings = settings if settings is not None else CompositionSettings.from_env()
    ports = build_lab_ports(settings)
    bundle_root = Path(os.environ.get("EDU_IT_BUNDLE_ROOT", ".")).resolve()
    content_root = os.environ.get("EDU_IT_CONTENT_ROOT", "content")
    store, _report = load_synced_store(content_root)
    lab = build_lab_worker(
        queue=ports.queue,
        sessions=ports.sessions,
        runs=ports.runs,
        mastery=ports.mastery,
        catalog=Content_LabCatalog(store),
        clock=ports.clock,
        bundle_root=bundle_root,
        settings=settings,
        setup_scripts=setup_script_resolver(store, bundle_root),
        poll_interval_s=poll_interval_s,
    )
    url = settings.database_url
    ai = build_ai_worker(
        queue=ports.queue,
        clock=ports.clock,
        ids=ports.ids,
        ledger=build_generation_ledger(settings, clock=ports.clock),
        parents=Content_ParentQuestionSource(store),
        variants=Postgres_VariantRepository(url, ports.clock),
        review_tasks=Postgres_ReviewTaskRepository(url, ports.clock),
        flags=Postgres_ItemFlagRepository(url, ports.clock),
        catalog=Postgres_CatalogRepository(url),
        quality_ports=QualityPorts(
            quality=Postgres_ItemQualityRepository(url, ports.clock),
            reports=Postgres_ProblemReportRepository(url),
            lessons=Postgres_LessonRepository(url),
            labs=Content_LabCatalog(store),
        ),
        advisor_ports=AdvisorPorts(
            targets=Postgres_AdvisorTargets(url),
            mastery=Postgres_MasteryRepository(url),
            reviews=Postgres_ReviewRepository(url),
            recommendations=Postgres_PathRecommendationRepository(url),
        ),
        retention_ports=RetentionPorts(
            quality=Postgres_ItemQualityRepository(url, ports.clock),
            reports=Postgres_ProblemReportRepository(url),
            recommendations=Postgres_PathRecommendationRepository(url),
        ),
        sandbox=build_sandbox_provider(settings, clock=ports.clock),
        settings=settings,
        poll_interval_s=poll_interval_s,
    )
    return ServedWorkers(lab=lab, ai=ai)


def main() -> None:
    """Start the lab and AI consumers and serve the health surface until interrupted."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
    workers = build_served_worker()
    health_app = create_health_app()
    workers.start()
    try:
        uvicorn.run(
            health_app,
            host=os.environ.get("WORKER_HEALTH_HOST", "127.0.0.1"),
            port=int(os.environ.get("WORKER_HEALTH_PORT", "8001")),
        )
    finally:
        workers.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
