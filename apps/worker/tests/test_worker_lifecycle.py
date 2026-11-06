"""Worker startup checks, shutdown drain, pod watch registration and restart recovery (task 8.8).

**Validates: Requirements 1.3, 2.9, 2.10, 14.10, 14.11, 20.5, 20.6, 20.7, 23.2**
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.ports.lab_pods import LabPodEvent, PodLossKind
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.composition import ProviderSelectionError
from edu_it_infrastructure.fakes import Fake_LabCatalog, Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.lab_pods import Fake_LabPodWatcher
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker import serve
from edu_it_worker.labs import LabWorker, build_lab_worker
from edu_it_worker.lifecycle import (
    REQUIRED_SECRET_KEYS,
    ShutdownCoordinator,
    StartupRefused,
    run_startup_checks,
    termination_grace_seconds,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)
SECRET_VALUE = "s3cr3t-value"


class _Secrets:
    def __init__(self, present: set[str]) -> None:
        self._present = present

    def read(self, key: str) -> str:
        if key not in self._present:
            raise KeyError(key)
        return SECRET_VALUE


ALL_SECRETS = _Secrets(set(REQUIRED_SECRET_KEYS))


# --- startup checks (Req 1.3, 2.9, 2.10, 23.2) -------------------------------------


def test_unsupported_sandbox_provider_is_refused() -> None:
    with pytest.raises(ProviderSelectionError, match="'podman'"):
        run_startup_checks({"SANDBOX_PROVIDER": "podman"})


def test_production_runc_is_refused_before_any_job() -> None:
    env = {
        "SANDBOX_PROVIDER": "kubernetes",
        "EDU_IT_ENV": "production",
        "SANDBOX_RUNTIME_KIND": "runc",
        "SANDBOX_RUNTIME_CLASS": "",
    }
    with pytest.raises(StartupRefused, match="runc"):
        run_startup_checks(env, ALL_SECRETS)


def test_development_runc_warns_once_and_starts(caplog: pytest.LogCaptureFixture) -> None:
    env = {
        "SANDBOX_PROVIDER": "kubernetes",
        "EDU_IT_ENV": "development",
        "SANDBOX_RUNTIME_KIND": "runc",
        "SANDBOX_RUNTIME_CLASS": "",
    }
    with caplog.at_level(logging.WARNING):
        settings = run_startup_checks(env, ALL_SECRETS)
    assert settings.sandbox_runtime_kind == "runc"
    assert [r for r in caplog.records if "Runc_Warning" in r.getMessage()].__len__() == 1


def test_gvisor_in_production_starts_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    env = {"SANDBOX_PROVIDER": "kubernetes", "EDU_IT_ENV": "production"}
    with caplog.at_level(logging.WARNING):
        run_startup_checks(env, ALL_SECRETS)
    assert caplog.records == []


def test_missing_secrets_are_named_and_no_value_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    env = {"SANDBOX_PROVIDER": "kubernetes"}
    with caplog.at_level(logging.DEBUG), pytest.raises(StartupRefused) as exc:
        run_startup_checks(env, _Secrets({"database/url"}))
    assert "redis/url" in str(exc.value)
    assert "database/url" not in str(exc.value)
    assert SECRET_VALUE not in caplog.text + str(exc.value)
    assert [r.getMessage() for r in caplog.records].count(
        "required secret is missing: key=redis/url"
    ) == 1


def test_secrets_are_not_required_for_a_local_fake_provider() -> None:
    settings = run_startup_checks({"SANDBOX_PROVIDER": "fake"}, _Secrets(set()))
    assert settings.sandbox_provider.value == "fake"


def test_secrets_root_env_enables_the_check_for_any_provider(tmp_path: Path) -> None:
    with pytest.raises(StartupRefused, match="database/url"):
        run_startup_checks({"EDU_IT_SECRETS_ROOT": str(tmp_path)})
    for key in REQUIRED_SECRET_KEYS:
        (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / key).write_text("v", encoding="utf-8")
    run_startup_checks({"EDU_IT_SECRETS_ROOT": str(tmp_path)})


def test_main_exits_non_zero_without_building_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_PROVIDER", "podman")

    def boom(**_kw: object) -> None:
        raise AssertionError("workers must not be built")

    monkeypatch.setattr(serve, "build_served_worker", boom)
    assert serve.main() == 1


def test_termination_grace_comes_from_env_with_a_default() -> None:
    assert termination_grace_seconds({"WORKER_TERMINATION_GRACE_SECONDS": "90"}) == 90
    assert termination_grace_seconds({}) == 60
    assert termination_grace_seconds({"WORKER_TERMINATION_GRACE_SECONDS": "x"}) == 60


# --- SIGTERM drain (Req 14.10, 14.11) ----------------------------------------------


class _Runtime:
    """Handlers finish after ``finish_at`` on the fake clock (never when ``None``)."""

    def __init__(self, now: _Now, finish_at: float | None) -> None:
        self.now = now
        self.finish_at = finish_at
        self.calls: list[tuple[str, float]] = []
        self.requeued = 0

    def stop_consuming(self) -> None:
        self.calls.append(("stop_consuming", self.now.t))

    def in_flight(self) -> bool:
        return self.finish_at is None or self.now.t < self.finish_at

    def requeue_in_flight(self) -> int:
        self.calls.append(("requeue", self.now.t))
        self.requeued += 1
        return 2


class _Now:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _coordinator(runtime: _Runtime, now: _Now, grace: float) -> ShutdownCoordinator:
    return ShutdownCoordinator(runtime, grace_s=grace, monotonic=now, sleep=now.sleep, poll_s=1.0)


def test_drain_stops_consuming_first_and_requeues_nothing_when_jobs_finish() -> None:
    now = _Now()
    runtime = _Runtime(now, finish_at=110.0)

    assert _coordinator(runtime, now, 60).drain() == 0

    assert runtime.calls == [("stop_consuming", 100.0)]
    assert now.t == 110.0  # waited only until the job finished


def test_drain_requeues_unfinished_jobs_at_grace_minus_five_seconds() -> None:
    now = _Now()
    runtime = _Runtime(now, finish_at=None)

    assert _coordinator(runtime, now, 60).drain() == 2

    assert runtime.calls == [("stop_consuming", 100.0), ("requeue", 155.0)]


def test_grace_not_above_the_margin_requeues_at_once() -> None:
    now = _Now()
    runtime = _Runtime(now, finish_at=None)

    _coordinator(runtime, now, 5).drain()

    assert runtime.calls == [("stop_consuming", 100.0), ("requeue", 100.0)]


def test_request_is_idempotent_and_wait_returns_the_requeued_count() -> None:
    now = _Now()
    runtime = _Runtime(now, finish_at=None)
    coordinator = _coordinator(runtime, now, 60)

    coordinator.request()
    coordinator.request()

    assert coordinator.wait(timeout=5) == 2
    assert coordinator.requested
    assert [c[0] for c in runtime.calls].count("stop_consuming") == 1
    assert runtime.requeued == 1


# --- lab worker start: interrupted runs and pod watch (Req 20.5-20.7) ---------------


def _session(sid: str, status: SessionStatus, sandbox_ref: str | None = None) -> LabSession:
    return LabSession(
        stable_id=sid,
        user_id=f"u-{sid}",
        lab_id="lab",
        status=status,
        created_at=T0,
        ttl_at=T0 + timedelta(hours=1),
        sandbox_ref=sandbox_ref,
    )


def _build(
    sessions: In_Memory_LabSessionRepository,
    runs: In_Memory_GradingRunRepository,
    **kw: object,
) -> tuple[LabWorker, In_Memory_TaskQueue]:
    clock = Test_Clock(T0)
    queue = In_Memory_TaskQueue()
    worker = build_lab_worker(
        queue=queue,
        sessions=sessions,
        runs=runs,
        mastery=None,  # type: ignore[arg-type]
        catalog=Fake_LabCatalog([]),
        clock=clock,
        bundle_root=Path("."),
        sandbox=Fake_SandboxProvider(clock),
        **kw,  # type: ignore[arg-type]
    )
    return worker, queue


def test_start_closes_interrupted_grading_runs_and_never_reruns_them() -> None:
    sessions, runs = In_Memory_LabSessionRepository(), In_Memory_GradingRunRepository()
    sessions.add_if_no_active(_session("s1", SessionStatus.GRADING))
    runs.add(GradingRun("r1", "s1", RunStatus.RUNNING, T0, T0 + timedelta(seconds=15)))
    worker, _queue = _build(sessions, runs)

    worker.start()
    try:
        run = runs.get("r1")
        assert run is not None and run.status is RunStatus.COULD_NOT_COMPLETE
        assert sessions.get("s1").status is SessionStatus.READY  # type: ignore[union-attr]
        # The redelivered grading job finds the run finished and grades nothing.
        worker.consumers["lab.grade"]({"run_id": "r1", "session_id": "s1"})
        again = runs.get("r1")
        assert again is not None
        assert again.status is RunStatus.COULD_NOT_COMPLETE and again.results == ()
    finally:
        worker.stop()


def test_start_registers_the_pod_watch_and_ends_a_lost_session() -> None:
    sessions, runs = In_Memory_LabSessionRepository(), In_Memory_GradingRunRepository()
    sessions.add_if_no_active(_session("s1", SessionStatus.READY, sandbox_ref="pod-1"))
    watcher = Fake_LabPodWatcher()
    watcher.script([LabPodEvent(SandboxHandle("pod-1", "s1", "kubernetes"), PodLossKind.EVICTED)])
    worker, _queue = _build(sessions, runs, pod_watcher=watcher)
    assert worker.pod_watch is not None

    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = sessions.get("s1")
            if current is not None and current.status is SessionStatus.ENDED_BY_INFRASTRUCTURE:
                break
            time.sleep(0.01)
        current = sessions.get("s1")
        assert current is not None and current.status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    finally:
        worker.stop()
    assert worker.watch_thread == []  # the watch stopped with the worker


def test_no_pod_watch_without_a_watcher() -> None:
    worker, _queue = _build(In_Memory_LabSessionRepository(), In_Memory_GradingRunRepository())
    assert worker.pod_watch is None


def test_stop_consuming_stops_the_scheduler_and_the_watch() -> None:
    worker, _queue = _build(
        In_Memory_LabSessionRepository(),
        In_Memory_GradingRunRepository(),
        pod_watcher=Fake_LabPodWatcher(),
    )
    worker.start()
    worker.stop_consuming()
    assert worker.watch_stop.is_set()
    assert worker.in_flight() is False
    worker.stop()
