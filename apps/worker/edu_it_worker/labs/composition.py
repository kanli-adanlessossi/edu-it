"""Lab worker composition: validate Config, pick the provider, wire consumers (Req 1.8, 6.1).

:func:`build_lab_worker` validates the lab Config first (before any provider call, so a
bad Config creates no Sandbox), selects the ``SandboxProvider`` with the shared
selection and production refusal (``build_sandbox_provider``), and assembles the use
cases, the consumers and the :class:`Scheduler`. Repositories, catalog, queue and clock
are injected, as in the other compositions.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from edu_it_application.labs import (
    DestroySandbox,
    EndByInfrastructure,
    GradeRun,
    ProvisionSandbox,
    PurgeLabData,
    ReapSessions,
    RecordCheckResult,
    WatchLabPods,
)
from edu_it_domain import config as _domain_config
from edu_it_domain.labs.config_rules import LabConfigError, validate_lab_config
from edu_it_infrastructure.composition import (
    CompositionSettings,
    SandboxBackend,
    build_k8s_api_port,
    build_sandbox_provider,
)
from edu_it_infrastructure.fakes.lab_metrics import NoopLabMetrics
from edu_it_infrastructure.sandbox.k8s_pod_watcher import KubernetesPodWatcher

from edu_it_worker.labs.jobs import (
    DESTROY_JOB,
    GRADE_JOB,
    PROVISION_JOB,
    REAP_JOB,
    RETAIN_JOB,
    SWEEP_JOB,
    SweepProvisioning,
    destroy_handler,
    grade_handler,
    provision_handler,
    reap_handler,
    retain_handler,
    sweep_handler,
)
from edu_it_worker.labs.scheduler import PeriodicJob, Scheduler

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from edu_it_application.labs import SetupScriptResolver
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lab_metrics import LabMetrics
    from edu_it_domain.ports.lab_pods import LabPodWatcher
    from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.sandbox import SandboxProvider
    from edu_it_domain.ports.task_queue import JobHandler, TaskQueue
    from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

__all__ = ["LabWorker", "build_lab_worker"]


@dataclass(frozen=True, slots=True)
class LabWorker:
    """The assembled lab consumers and scheduler."""

    queue: TaskQueue
    scheduler: Scheduler
    sandbox: SandboxProvider
    consumers: Mapping[str, JobHandler]
    threads: list[threading.Thread] = field(default_factory=list)
    pod_watch: WatchLabPods | None = None
    """The ``watch_lab_pods`` job; ``None`` when the provider has no pods to watch."""
    close_interrupted_runs: Callable[[], int] | None = None
    """Closes runs a restart interrupted; runs once, before the first job is consumed."""
    watch_stop: threading.Event = field(default_factory=threading.Event)
    watch_thread: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        """Close interrupted runs, then start the consumers, the scheduler and the pod watch.

        Interrupted grading runs are marked failed first, so a redelivered ``lab.grade`` job
        finds its run finished and never re-runs it (Req 20.6). ``TaskQueue.consume`` may
        block for the life of the process (Redis) or return after registering the handler
        (in-memory), so each consumer runs on its own thread.
        """
        if self.close_interrupted_runs is not None:
            closed = self.close_interrupted_runs()
            if closed:
                logging.getLogger(__name__).warning(
                    "closed %d grading run(s) interrupted by a restart", closed
                )
        if self.pod_watch is not None:
            watcher = threading.Thread(
                target=self.pod_watch.run,
                args=(self.watch_stop.is_set,),
                name="watch-lab-pods",
                daemon=True,
            )
            watcher.start()
            self.watch_thread.append(watcher)
        for job_name, handler in self.consumers.items():
            thread = threading.Thread(
                target=self.queue.consume,
                args=(job_name, handler),
                name=f"consumer-{job_name}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)
        self.scheduler.start()

    def stop_consuming(self) -> None:
        """Stop taking new work: scheduler, pod watch and the queue's delivery loops.

        A job whose handler is running is not interrupted; it finishes or is re-queued by
        the shutdown coordinator (Req 14.10).
        """
        self.scheduler.stop()
        self.watch_stop.set()
        stop = getattr(self.queue, "stop", None)
        if callable(stop):
            stop()

    def in_flight(self) -> bool:
        """Whether a consumer thread is still inside a handler."""
        return any(thread.is_alive() for thread in self.threads)

    def stop(self) -> None:
        """Stop the scheduler and ask the queue (when it supports it) to stop consuming."""
        self.stop_consuming()
        for thread in (*self.threads, *self.watch_thread):
            thread.join(timeout=5.0)
        self.threads.clear()
        self.watch_thread.clear()


def build_lab_worker(
    *,
    queue: TaskQueue,
    sessions: LabSessionRepository,
    runs: GradingRunRepository,
    mastery: MasteryRepository,
    catalog: LabCatalog,
    clock: Clock,
    bundle_root: Path,
    settings: CompositionSettings | None = None,
    sandbox: SandboxProvider | None = None,
    setup_scripts: SetupScriptResolver | None = None,
    lab_config: object = _domain_config,
    poll_interval_s: float = 1.0,
    metrics: LabMetrics | None = None,
    pod_watcher: LabPodWatcher | None = None,
    k8s_api: K8sApiPort | None = None,
) -> LabWorker:
    """Validate Config, select the provider and register the six lab consumers.

    Raises :class:`LabConfigError` for an invalid Config and
    ``ProviderSelectionError`` for ``docker`` in production, both before any Sandbox
    exists. ``sandbox`` overrides provider selection (tests).

    With ``SANDBOX_PROVIDER=kubernetes`` the ``watch_lab_pods`` job is registered over a
    :class:`KubernetesPodWatcher` that shares the provider's ``k8s_api``; ``pod_watcher``
    overrides it (tests). The watch and the worker's interrupted-run recovery run from
    :meth:`LabWorker.start`.
    """
    try:
        validate_lab_config(lab_config)
    except LabConfigError as exc:
        logging.getLogger(__name__).critical(
            "refusing to start: invalid lab Config", extra={"keys": list(exc.keys)}
        )
        raise
    settings = settings if settings is not None else CompositionSettings.from_env()
    watch_stop = threading.Event()
    if (
        pod_watcher is None
        and settings.sandbox_provider is SandboxBackend.KUBERNETES
        and (k8s_api is not None or sandbox is None)
    ):
        if k8s_api is None:
            k8s_api = build_k8s_api_port(settings)
        pod_watcher = KubernetesPodWatcher(k8s_api, settings.k8s_namespace, stop=watch_stop)
    if sandbox is None:
        sandbox = build_sandbox_provider(settings, clock=clock, k8s_api=k8s_api)
    provider_name = settings.sandbox_provider.value
    # The infrastructure-loss use case needs a metrics sink; the serve entrypoint passes the
    # process's PrometheusLabMetrics, tests and the no-metrics path fall back to the no-op.
    loss_metrics = metrics if metrics is not None else NoopLabMetrics()
    end_by_infrastructure = (
        EndByInfrastructure(sessions=sessions, clock=clock, metrics=loss_metrics)
        if metrics is not None or pod_watcher is not None
        else None
    )

    record = RecordCheckResult(runs=runs, mastery=mastery, clock=clock)
    reaper = ReapSessions(
        sessions=sessions,
        runs=runs,
        sandbox=sandbox,
        clock=clock,
        provider_name=provider_name,
        config=lab_config,
        end_by_infrastructure=end_by_infrastructure,
        pod_watcher=pod_watcher,
        metrics=metrics,
    )
    consumers = {
        PROVISION_JOB: provision_handler(
            ProvisionSandbox(
                sessions=sessions,
                catalog=catalog,
                sandbox=sandbox,
                clock=clock,
                setup_scripts=setup_scripts,
                config=lab_config,
                metrics=metrics,
            )
        ),
        GRADE_JOB: grade_handler(
            GradeRun(
                sessions=sessions,
                runs=runs,
                catalog=catalog,
                sandbox=sandbox,
                record=record,
                clock=clock,
                bundle_root=bundle_root,
                provider_name=provider_name,
                config=lab_config,
            )
        ),
        DESTROY_JOB: destroy_handler(
            DestroySandbox(
                sessions=sessions,
                runs=runs,
                sandbox=sandbox,
                clock=clock,
                provider_name=provider_name,
            )
        ),
        REAP_JOB: reap_handler(reaper),
        RETAIN_JOB: retain_handler(
            PurgeLabData(
                sessions=sessions,
                clock=clock,
                retention=lab_config.LAB_RETENTION_PERIOD,  # type: ignore[attr-defined]
            )
        ),
        SWEEP_JOB: sweep_handler(SweepProvisioning(sessions=sessions, queue=queue, clock=clock)),
    }
    scheduler = Scheduler(
        queue=queue,
        clock=clock,
        jobs=[
            PeriodicJob(REAP_JOB, lab_config.LAB_REAPER_INTERVAL),  # type: ignore[attr-defined]
            PeriodicJob(SWEEP_JOB, lab_config.LAB_SWEEP_INTERVAL),  # type: ignore[attr-defined]
            PeriodicJob(RETAIN_JOB, lab_config.LAB_RETENTION_INTERVAL),  # type: ignore[attr-defined]
        ],
        poll_interval_s=poll_interval_s,
    )
    pod_watch = (
        WatchLabPods(
            watcher=pod_watcher,
            end=end_by_infrastructure,  # type: ignore[arg-type]  # set whenever a watcher exists
            sandbox=sandbox,
            metrics=loss_metrics,
            sleep=lambda seconds: _wait(watch_stop, seconds),
        )
        if pod_watcher is not None
        else None
    )
    return LabWorker(
        queue=queue,
        scheduler=scheduler,
        sandbox=sandbox,
        consumers=consumers,
        pod_watch=pod_watch,
        close_interrupted_runs=reaper.close_interrupted_runs,
        watch_stop=watch_stop,
    )


def _wait(stop: threading.Event, seconds: float) -> None:
    """Sleep ``seconds`` but wake at once when ``stop`` is set."""
    stop.wait(seconds)
