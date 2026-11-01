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
    GradeRun,
    ProvisionSandbox,
    PurgeLabData,
    ReapSessions,
    RecordCheckResult,
)
from edu_it_domain import config as _domain_config
from edu_it_domain.labs.config_rules import LabConfigError, validate_lab_config
from edu_it_infrastructure.composition import CompositionSettings, build_sandbox_provider

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
    from collections.abc import Mapping
    from pathlib import Path

    from edu_it_application.labs import SetupScriptResolver
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
    from edu_it_domain.ports.mastery import MasteryRepository
    from edu_it_domain.ports.sandbox import SandboxProvider
    from edu_it_domain.ports.task_queue import JobHandler, TaskQueue

__all__ = ["LabWorker", "build_lab_worker"]


@dataclass(frozen=True, slots=True)
class LabWorker:
    """The assembled lab consumers and scheduler."""

    queue: TaskQueue
    scheduler: Scheduler
    sandbox: SandboxProvider
    consumers: Mapping[str, JobHandler]
    threads: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        """Start one consumer thread per job and the scheduler thread.

        ``TaskQueue.consume`` may block for the life of the process (Redis) or return
        after registering the handler (in-memory), so each runs on its own thread.
        """
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

    def stop(self) -> None:
        """Stop the scheduler and ask the queue (when it supports it) to stop consuming."""
        self.scheduler.stop()
        stop = getattr(self.queue, "stop", None)
        if callable(stop):
            stop()
        for thread in self.threads:
            thread.join(timeout=5.0)
        self.threads.clear()


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
) -> LabWorker:
    """Validate Config, select the provider and register the six lab consumers.

    Raises :class:`LabConfigError` for an invalid Config and
    ``ProviderSelectionError`` for ``docker`` in production, both before any Sandbox
    exists. ``sandbox`` overrides provider selection (tests).
    """
    try:
        validate_lab_config(lab_config)
    except LabConfigError as exc:
        logging.getLogger(__name__).critical(
            "refusing to start: invalid lab Config", extra={"keys": list(exc.keys)}
        )
        raise
    settings = settings if settings is not None else CompositionSettings.from_env()
    if sandbox is None:
        sandbox = build_sandbox_provider(settings, clock=clock)
    provider_name = settings.sandbox_provider.value

    record = RecordCheckResult(runs=runs, mastery=mastery, clock=clock)
    consumers = {
        PROVISION_JOB: provision_handler(
            ProvisionSandbox(
                sessions=sessions,
                catalog=catalog,
                sandbox=sandbox,
                clock=clock,
                setup_scripts=setup_scripts,
                config=lab_config,
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
        REAP_JOB: reap_handler(
            ReapSessions(
                sessions=sessions,
                runs=runs,
                sandbox=sandbox,
                clock=clock,
                provider_name=provider_name,
                config=lab_config,
            )
        ),
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
    return LabWorker(queue=queue, scheduler=scheduler, sandbox=sandbox, consumers=consumers)
