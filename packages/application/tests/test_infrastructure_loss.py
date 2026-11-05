"""Unit tests for infrastructure loss handling and the reaper extension (task 8.7).

Requirements 6.6, 6.7, 19.2-19.5, 19.7, 19.8, 20.3, 20.7.
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_application.labs import (
    EndByInfrastructure,
    ProvisionSandbox,
    ReapSessions,
    WatchLabPods,
)
from edu_it_domain import config
from edu_it_domain.labs.k8s_rules import reconnect_delays
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.lab_pods import LabPodEvent, PodLossKind
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.fakes.lab_metrics import NoopLabMetrics
from edu_it_infrastructure.fakes.lab_pods import Fake_LabPodWatcher

_LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)


class _Metrics(NoopLabMetrics):
    def __init__(self) -> None:
        self.infra_loss = 0
        self.deletions: list[str] = []

    def inc_infrastructure_loss(self) -> None:
        self.infra_loss += 1

    def inc_reaper_deletion(self, *, reason: str) -> None:
        self.deletions.append(reason)


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.metrics = _Metrics()
        self.watcher = Fake_LabPodWatcher()
        self.end = EndByInfrastructure(
            sessions=self.sessions, clock=self.clock, metrics=self.metrics
        )
        self.provisioner = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([_LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
        )
        self.reaper = ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
            end_by_infrastructure=self.end,
            pod_watcher=self.watcher,
            metrics=self.metrics,
        )

    def ready_session(self, sid: str = "s1", user: str = "u1") -> LabSession:
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=sid,
                user_id=user,
                lab_id="lab1",
                status=SessionStatus.PROVISIONING,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
            )
        )
        self.provisioner.execute(sid)
        return self.get(sid)

    def get(self, sid: str = "s1") -> LabSession:
        session = self.sessions.get(sid)
        assert session is not None
        return session

    def handle(self, sid: str = "s1") -> SandboxHandle:
        ref = self.get(sid).sandbox_ref
        assert ref is not None
        return SandboxHandle(ref, sid, "fake")

    def alive(self, sid: str = "s1") -> bool:
        return not self.sandbox.sandbox(self.handle(sid)).destroyed


# -- EndByInfrastructure ---------------------------------------------------------


def test_end_by_infrastructure_ends_frees_slot_and_counts_once() -> None:
    env = _Env()
    env.ready_session()

    assert env.end.execute("s1") is True
    session = env.get()
    assert session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert session.ended_at == env.clock.now()
    assert env.sessions.get_active_for_user("u1") is None  # slot free: no cooldown (19.3)
    assert env.metrics.infra_loss == 1

    for _ in range(3):  # repeated detection never counts again (19.7)
        assert env.end.execute("s1") is False
    assert env.metrics.infra_loss == 1


def test_end_by_infrastructure_ignores_absent_provisioning_and_terminal_sessions() -> None:
    env = _Env()
    assert env.end.execute("nope") is False

    now = env.clock.now()
    env.sessions.add_if_no_active(
        LabSession(
            stable_id="p1",
            user_id="u2",
            lab_id="lab1",
            status=SessionStatus.PROVISIONING,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
        )
    )
    assert env.end.execute("p1") is False
    assert env.get("p1").status is SessionStatus.PROVISIONING

    env.ready_session("s1")
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    assert env.end.execute("s1") is False
    assert env.get("s1").status is SessionStatus.ENDED
    assert env.metrics.infra_loss == 0


def test_end_by_infrastructure_applies_to_a_grading_session() -> None:
    env = _Env()
    env.ready_session()
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.GRADING)
    assert env.end.execute("s1") is True
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE


# -- WatchLabPods ------------------------------------------------------------------


def _watch_job(env: _Env, sleeps: list[float] | None = None) -> WatchLabPods:
    log = sleeps if sleeps is not None else []
    return WatchLabPods(
        watcher=env.watcher,
        end=env.end,
        sandbox=env.sandbox,
        metrics=env.metrics,
        sleep=log.append,
    )


def test_evicted_event_ends_the_session_and_removes_the_pod_object() -> None:
    env = _Env()
    env.ready_session()
    handle = env.handle()

    _watch_job(env).handle(LabPodEvent(handle, PodLossKind.EVICTED))

    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert not env.alive()  # evicted pod object removed (19.5)
    assert env.metrics.infra_loss == 1
    assert env.metrics.deletions == ["evicted"]


def test_deleted_event_ends_the_session_without_a_destroy_call() -> None:
    env = _Env()
    env.ready_session()
    handle = env.handle()
    env.sandbox.vanish(handle)
    env.sandbox.calls.clear()

    _watch_job(env).handle(LabPodEvent(handle, PodLossKind.DELETED))

    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert "destroy" not in env.sandbox.calls


def test_repeated_events_count_the_loss_once() -> None:
    env = _Env()
    env.ready_session()
    job = _watch_job(env)
    event = LabPodEvent(env.handle(), PodLossKind.FAILED)
    for _ in range(3):
        job.handle(event)
    assert env.metrics.infra_loss == 1


def test_an_event_for_an_unrelated_session_changes_nothing() -> None:
    env = _Env()
    env.ready_session()
    stranger = SandboxHandle("fake-x", "unknown", "fake")
    _watch_job(env).handle(LabPodEvent(stranger, PodLossKind.EVICTED))
    assert env.get().status is SessionStatus.READY
    assert env.metrics.infra_loss == 0


def test_event_handling_survives_a_failing_destroy() -> None:
    env = _Env()
    env.ready_session()
    handle = env.handle()

    def boom(_handle: SandboxHandle) -> None:
        raise RuntimeError("api down")

    env.sandbox.destroy = boom  # type: ignore[method-assign]
    _watch_job(env).handle(LabPodEvent(handle, PodLossKind.EVICTED))
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.metrics.deletions == []  # not counted; the reaper retries


def test_run_reconnects_with_backoff_and_resets_after_a_delivering_stream() -> None:
    env = _Env()
    env.ready_session()
    env.watcher.script(RuntimeError("stream broke"))
    env.watcher.script(RuntimeError("still down"))
    env.watcher.script([LabPodEvent(env.handle(), PodLossKind.EVICTED)])
    env.watcher.script(RuntimeError("down again"))
    sleeps: list[float] = []
    job = _watch_job(env, sleeps)

    run_to = {"calls": 4}
    job.run(lambda: env.watcher.watch_calls >= run_to["calls"])

    delays = [d.total_seconds() for d in reconnect_delays("restart")]
    assert sleeps == [delays[0], delays[1], delays[0]]  # reset after the delivering stream
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE


def test_run_repeats_the_last_delay_when_the_schedule_is_exhausted() -> None:
    env = _Env()
    delays = [d.total_seconds() for d in reconnect_delays("restart")]
    sleeps: list[float] = []
    job = _watch_job(env, sleeps)
    total = len(delays) + 3
    job.run(lambda: env.watcher.watch_calls >= total)
    assert sleeps[: len(delays)] == delays
    assert set(sleeps[len(delays) :]) == {delays[-1]}


def test_run_stops_without_watching_when_already_stopped() -> None:
    env = _Env()
    _watch_job(env).run(lambda: True)
    assert env.watcher.watch_calls == 0


# -- Reaper extension ----------------------------------------------------------------


def test_reaper_ends_a_session_whose_pod_vanished_once() -> None:
    env = _Env()
    env.ready_session()
    env.sandbox.vanish(env.handle())

    first = env.reaper.execute()
    assert first.infra_lost == 1
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sessions.get_active_for_user("u1") is None

    second = env.reaper.execute()
    assert second.infra_lost == 0
    assert env.metrics.infra_loss == 1  # exactly once per session (19.7)


def test_reaper_ends_a_session_with_an_evicted_pod_and_removes_the_pod_in_the_same_tick() -> None:
    env = _Env()
    env.ready_session()
    handle = env.handle()
    env.watcher.failed.add(handle.sandbox_id)

    report = env.reaper.execute()

    assert report.infra_lost == 1
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert not env.alive()  # within 60 s of detection (19.5)
    assert "evicted" in env.metrics.deletions


def test_reaper_deletes_a_failed_pod_of_an_ended_session_and_orphans() -> None:
    env = _Env()
    env.ready_session("s1", "u1")
    env.ready_session("s2", "u2")
    s1_pod = env.handle("s1")
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    env.watcher.failed.add(s1_pod.sandbox_id)

    env.reaper.execute()

    assert not env.alive("s1")
    assert env.alive("s2")  # an active session's pod is never selected (20.3)
    assert env.get("s2").status is SessionStatus.READY


def test_reaper_resumes_from_persisted_state_after_a_restart() -> None:
    env = _Env()
    env.ready_session()
    env.sandbox.vanish(env.handle())
    env.clock.advance(timedelta(seconds=5))

    # A brand-new reaper instance holds no memory of earlier ticks (20.7).
    fresh = ReapSessions(
        sessions=env.sessions,
        runs=env.runs,
        sandbox=env.sandbox,
        clock=env.clock,
        provider_name="fake",
        end_by_infrastructure=env.end,
        pod_watcher=env.watcher,
        metrics=env.metrics,
    )
    assert fresh.execute().infra_lost == 1
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE


def test_reaper_without_infrastructure_wiring_keeps_a_session_with_a_missing_pod() -> None:
    env = _Env()
    env.ready_session()
    env.sandbox.vanish(env.handle())
    legacy = ReapSessions(
        sessions=env.sessions,
        runs=env.runs,
        sandbox=env.sandbox,
        clock=env.clock,
        provider_name="fake",
    )
    assert legacy.execute().infra_lost == 0
    assert env.get().status is SessionStatus.READY
