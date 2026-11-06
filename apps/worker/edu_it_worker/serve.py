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
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from edu_it_content.lab_runtime import load_synced_store, setup_script_resolver
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.adapters.parent_questions import Content_ParentQuestionSource
from edu_it_infrastructure.composition import (
    CompositionSettings,
    ProviderSelectionError,
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
from edu_it_infrastructure.telemetry import (
    MetricsPortError,
    PrometheusLabMetrics,
    configure_logging,
    configure_tracing_from_env,
    resolve_metrics_port,
    shutdown_tracing,
    start_metrics_server,
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
from edu_it_worker.lifecycle import (
    ShutdownCoordinator,
    StartupRefused,
    run_startup_checks,
    termination_grace_seconds,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from types import FrameType
    from wsgiref.simple_server import WSGIServer

    from edu_it_domain.ports.lab_metrics import LabMetrics
    from opentelemetry.sdk.trace import TracerProvider
    from prometheus_client import CollectorRegistry

__all__ = [
    "ServedWorkers",
    "WorkerTelemetry",
    "build_lab_worker",
    "build_served_worker",
    "main",
    "start_telemetry",
]

METRICS_PORT_VARIABLE = "WORKER_METRICS_PORT"
METRICS_HOST_VARIABLE = "WORKER_METRICS_HOST"
_DEFAULT_METRICS_PORT = 9090


@dataclass(frozen=True, slots=True)
class WorkerTelemetry:
    """The per-process telemetry: the one Prometheus Lab_Metrics and the tracer provider."""

    metrics: PrometheusLabMetrics
    metrics_server: WSGIServer
    tracer_provider: TracerProvider | None

    def shutdown(self) -> None:
        """Stop the metrics endpoint and flush the trace exporter (best effort)."""
        try:
            self.metrics_server.shutdown()
            self.metrics_server.server_close()
        finally:
            shutdown_tracing(self.tracer_provider)


def start_telemetry(
    environ: Mapping[str, str],
    *,
    traffic_ports: Collection[int],
    registry: CollectorRegistry | None = None,
) -> WorkerTelemetry:
    """Create the process's one ``PrometheusLabMetrics`` and serve it on the metrics port.

    The port (``WORKER_METRICS_PORT``, default 9090) must differ from the health port
    (Req 13.3); a clash or malformed value raises :class:`StartupRefused`. Tracing comes from
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` and is a no-op without it (Req 13.1).
    """
    try:
        port = resolve_metrics_port(
            environ,
            METRICS_PORT_VARIABLE,
            default=_DEFAULT_METRICS_PORT,
            traffic_ports=traffic_ports,
        )
    except MetricsPortError as exc:
        raise StartupRefused(str(exc)) from exc
    metrics = PrometheusLabMetrics(registry)
    provider = configure_tracing_from_env(environ, service_name="edu-it-worker")
    try:
        server, _thread = start_metrics_server(
            port, host=environ.get(METRICS_HOST_VARIABLE, "127.0.0.1"), registry=registry
        )
    except OSError as exc:
        shutdown_tracing(provider)
        raise StartupRefused(f"{METRICS_PORT_VARIABLE} cannot be bound") from exc
    return WorkerTelemetry(metrics=metrics, metrics_server=server, tracer_provider=provider)


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

    def stop_consuming(self) -> None:
        """Stop taking new jobs in both workers (SIGTERM); running handlers finish."""
        try:
            self.ai.stop_consuming()
        finally:
            self.lab.stop_consuming()

    def in_flight(self) -> bool:
        """Whether a handler of either worker is still running."""
        return self.lab.in_flight() or self.ai.in_flight()

    def requeue_in_flight(self) -> int:
        """Re-queue the jobs whose handler has not returned, payload and attempts untouched.

        Both workers share one queue, so one call covers them. A queue without the
        capability (the in-memory fake) has nothing durable to re-queue.
        """
        requeue = getattr(self.lab.queue, "requeue_in_flight", None)
        return int(requeue()) if callable(requeue) else 0


def build_served_worker(
    *,
    settings: CompositionSettings | None = None,
    poll_interval_s: float = 1.0,
    metrics: LabMetrics | None = None,
) -> ServedWorkers:
    """Compose the lab and AI workers over the real adapters (nothing is started).

    ``metrics`` is the process's Lab_Metrics; it reaches the provisioning, reaper and
    infrastructure-loss use cases of the lab worker (Req 13.3, 13.4).
    """
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
        metrics=metrics,
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


class _DrainingServer(uvicorn.Server):
    """A uvicorn server whose SIGTERM also starts the Worker drain (Req 14.10)."""

    def __init__(self, config: uvicorn.Config, coordinator: ShutdownCoordinator) -> None:
        super().__init__(config)
        self._coordinator = coordinator

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGTERM:
            self._coordinator.request()
        super().handle_exit(sig, frame)


def main() -> int:
    """Check the start-up conditions, consume jobs and serve health until SIGTERM.

    Returns the process exit status: ``1`` when a start-up condition refuses the start
    (nothing was consumed), ``0`` after a drained shutdown.
    """
    # JSON records carry the request id the Worker binds from each job envelope.
    level_name = os.environ.get("LOG_LEVEL", "info").upper()
    configure_logging(level=logging.getLevelNamesMapping().get(level_name, logging.INFO))
    log = logging.getLogger(__name__)
    health_port = int(os.environ.get("WORKER_HEALTH_PORT", "8001"))
    try:
        settings = run_startup_checks(os.environ)
        telemetry = start_telemetry(os.environ, traffic_ports={health_port})
    except (StartupRefused, ProviderSelectionError) as exc:
        log.critical("%s", exc)
        return 1

    try:
        return _run(settings, telemetry, health_port)
    finally:
        telemetry.shutdown()


def _run(settings: CompositionSettings, telemetry: WorkerTelemetry, health_port: int) -> int:
    """Consume jobs and serve health until SIGTERM; the one metrics sink goes to the lab worker."""
    workers = build_served_worker(settings=settings, metrics=telemetry.metrics)
    coordinator = ShutdownCoordinator(workers, grace_s=termination_grace_seconds(os.environ))
    # Installed before uvicorn: uvicorn restores it and re-raises a captured SIGTERM to it, so
    # the process stays alive until the drain below has finished.
    signal.signal(signal.SIGTERM, coordinator.request)
    server = _DrainingServer(
        uvicorn.Config(
            create_health_app(),
            host=os.environ.get("WORKER_HEALTH_HOST", "127.0.0.1"),
            port=health_port,
        ),
        coordinator,
    )
    workers.start()
    try:
        server.run()
    finally:
        coordinator.request()  # no-op after SIGTERM; drains on any other exit
        requeued = coordinator.wait()
        if not requeued:  # handlers left running are daemon threads: exit without joining them
            workers.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
