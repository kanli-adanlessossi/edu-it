"""Unit tests for the lab worker scheduler, sweep, handlers and composition (task 6.17).

**Validates: Requirements 1.8, 6.1, 6.5, 8.1, 9.1**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_application.labs import PROVISION_JOB
from edu_it_domain import config as cfg
from edu_it_domain.labs.config_rules import LAB_CONFIG_KEYS, LabConfigError
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    ProviderSelectionError,
    SandboxBackend,
)
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    Test_Clock,
)
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker.labs import PeriodicJob, Scheduler, SweepProvisioning, build_lab_worker
from edu_it_worker.labs.jobs import (
    REAP_JOB,
    RETAIN_JOB,
    SWEEP_JOB,
    provision_handler,
    reap_handler,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _session(sid: str, status: SessionStatus, **kw: object) -> LabSession:
    return LabSession(
        stable_id=sid,
        user_id=f"u-{sid}",
        lab_id="lab",
        status=status,
        created_at=T0,
        ttl_at=T0 + timedelta(hours=1),
        **kw,  # type: ignore[arg-type]
    )


def test_scheduler_enqueues_once_per_bucket_and_again_in_the_next() -> None:
    clock, queue = Test_Clock(T0), In_Memory_TaskQueue()
    sched = Scheduler(queue=queue, clock=clock, jobs=[PeriodicJob(REAP_JOB, timedelta(seconds=30))])

    assert sched.tick() == [REAP_JOB]
    assert sched.tick() == []  # same bucket
    clock.advance(timedelta(seconds=10))
    assert sched.tick() == []
    clock.advance(timedelta(seconds=30))
    assert sched.tick() == [REAP_JOB]
    assert len(queue.pending_for(REAP_JOB)) == 2


def test_two_schedulers_in_one_bucket_enqueue_one_pending_job() -> None:
    clock, queue = Test_Clock(T0), In_Memory_TaskQueue()
    jobs = [PeriodicJob(SWEEP_JOB, timedelta(seconds=5))]
    Scheduler(queue=queue, clock=clock, jobs=jobs).tick()
    Scheduler(queue=queue, clock=clock, jobs=jobs).tick()
    assert len(queue.pending_for(SWEEP_JOB)) == 1


def test_periodic_job_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        PeriodicJob(REAP_JOB, timedelta(0))


def test_sweep_requeues_due_provisioning_sessions_only() -> None:
    clock, queue = Test_Clock(T0), In_Memory_TaskQueue()
    sessions = In_Memory_LabSessionRepository()
    sessions.add_if_no_active(_session("stuck", SessionStatus.PROVISIONING))
    sessions.add_if_no_active(
        _session("due", SessionStatus.PROVISIONING, next_provision_at=T0 - timedelta(seconds=1))
    )
    sessions.add_if_no_active(
        _session("backoff", SessionStatus.PROVISIONING, next_provision_at=T0 + timedelta(seconds=9))
    )
    sessions.add_if_no_active(_session("ready", SessionStatus.READY))
    sweep = SweepProvisioning(sessions=sessions, queue=queue, clock=clock)

    assert sweep.execute() == 2
    assert sweep.execute() == 2  # dedupe collapses the repeat
    ids = {j.payload["session_id"] for j in queue.pending_for(PROVISION_JOB)}
    assert ids == {"stuck", "due"}
    assert len(queue.pending_for(PROVISION_JOB)) == 2


def test_sweep_continues_after_an_enqueue_failure() -> None:
    clock, inner = Test_Clock(T0), In_Memory_TaskQueue()

    class Flaky:
        def enqueue(self, job_name, payload, *, dedupe_key=None):  # type: ignore[no-untyped-def]
            if payload["session_id"] == "a":
                raise RuntimeError("redis down")
            inner.enqueue(job_name, payload, dedupe_key=dedupe_key)

    sessions = In_Memory_LabSessionRepository()
    sessions.add_if_no_active(_session("a", SessionStatus.PROVISIONING))
    sessions.add_if_no_active(_session("b", SessionStatus.PROVISIONING))

    assert SweepProvisioning(sessions=sessions, queue=Flaky(), clock=clock).execute() == 1  # type: ignore[arg-type]
    assert [j.payload["session_id"] for j in inner.pending_for(PROVISION_JOB)] == ["b"]


def test_handlers_swallow_errors_and_bad_payloads() -> None:
    class Boom:
        def execute(self, *_a: object) -> None:
            raise RuntimeError("boom")

    reap_handler(Boom())({})  # type: ignore[arg-type]
    provision_handler(Boom())({})  # type: ignore[arg-type]  # missing session_id


def _build(**kw: object):  # type: ignore[no-untyped-def]
    clock = kw.pop("clock", Test_Clock(T0))
    queue = In_Memory_TaskQueue()
    worker = build_lab_worker(
        queue=queue,
        sessions=In_Memory_LabSessionRepository(),
        runs=In_Memory_GradingRunRepository(),
        mastery=None,  # type: ignore[arg-type]
        catalog=Fake_LabCatalog([]),
        clock=clock,  # type: ignore[arg-type]
        bundle_root=Path("."),
        **kw,  # type: ignore[arg-type]
    )
    return worker, queue


def test_composition_registers_six_consumers_and_schedules_three_jobs() -> None:
    worker, queue = _build(settings=CompositionSettings())
    assert set(worker.consumers) == {
        "lab.provision",
        "lab.grade",
        "lab.destroy",
        "lab.reap",
        "lab.retain",
        "lab.sweep",
    }
    assert set(worker.scheduler.tick()) == {REAP_JOB, SWEEP_JOB, RETAIN_JOB}
    assert {j.job_name for j in queue.pending} == {REAP_JOB, SWEEP_JOB, RETAIN_JOB}
    worker.start()  # in-memory consume returns at once; scheduler thread starts
    worker.stop()
    assert worker.threads == []


def test_composition_refuses_invalid_config_before_provider_call() -> None:
    config = {k: getattr(cfg, k) for k in LAB_CONFIG_KEYS}
    config["LAB_IDLE_TIMEOUT"] = timedelta(0)
    provider = Fake_SandboxProvider(Test_Clock(T0))
    with pytest.raises(LabConfigError):
        _build(lab_config=type("C", (), config), sandbox=provider)
    assert provider.calls == []


def test_composition_refuses_docker_in_production() -> None:
    settings = CompositionSettings(
        environment=Environment.PRODUCTION, sandbox_provider=SandboxBackend.DOCKER
    )
    with pytest.raises(ProviderSelectionError):
        _build(settings=settings)
