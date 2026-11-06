"""Worker restart and infrastructure loss: reruns, interrupted runs, reaper resume (task 8.10).

Complements ``test_worker_lifecycle.py`` (startup/drain/pod-watch registration) and
``packages/application/tests/test_infrastructure_loss.py`` (use-case level) with the
crash-recovery cases and the guarantee that learner progress is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from edu_it_application.labs import (
    EndByInfrastructure,
    ProvisionOutcome,
    ProvisionSandbox,
    ReapSessions,
    WatchLabPods,
)
from edu_it_domain import config
from edu_it_domain.labs.models import GradingRun, LabSession, RunStatus, SessionStatus
from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    MasteryEvent,
    MasteryState,
    QuizAttempt,
)
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.lab_pods import LabPodEvent, PodLossKind
from edu_it_domain.ports.progress import LessonCompletionRecord, ModuleProgressRecord
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import Fake_LabCatalog, Fake_SandboxProvider, Test_Clock
from edu_it_infrastructure.fakes.lab_metrics import NoopLabMetrics
from edu_it_infrastructure.fakes.lab_pods import Fake_LabPodWatcher
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.fakes.learning_repositories import (
    In_Memory_AttemptRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
)
from edu_it_infrastructure.fakes.sandbox_provider import ProvisionMode
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_worker.labs import LabWorker, build_lab_worker

T0 = datetime(2024, 1, 1, tzinfo=UTC)
LAB = LabDefinition(
    lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
)


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock(T0)
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.watcher = Fake_LabPodWatcher()
        self.metrics = NoopLabMetrics()
        self.provisioner = ProvisionSandbox(
            sessions=self.sessions,
            catalog=Fake_LabCatalog([LAB]),
            sandbox=self.sandbox,
            clock=self.clock,
        )
        self.end = EndByInfrastructure(
            sessions=self.sessions, clock=self.clock, metrics=self.metrics
        )

    def reaper(self) -> ReapSessions:
        """A brand-new reaper: it holds no memory of earlier ticks."""
        return ReapSessions(
            sessions=self.sessions,
            runs=self.runs,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
            end_by_infrastructure=self.end,
            pod_watcher=self.watcher,
            metrics=self.metrics,
        )

    def add_provisioning(self, sid: str = "s1", user: str = "u1") -> None:
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

    def get(self, sid: str = "s1") -> LabSession:
        session = self.sessions.get(sid)
        assert session is not None
        return session

    def handle(self, sid: str = "s1") -> SandboxHandle:
        ref = self.get(sid).sandbox_ref
        assert ref is not None
        return SandboxHandle(ref, sid, "fake")


def _learner_data() -> tuple[
    In_Memory_MasteryRepository, In_Memory_AttemptRepository, In_Memory_ProgressRepository
]:
    mastery = In_Memory_MasteryRepository()
    attempts = In_Memory_AttemptRepository()
    progress = In_Memory_ProgressRepository()
    mastery.save(
        Mastery(
            user_id="u1",
            concept_id="c1",
            score=62,
            state=MasteryState.PRACTICED,
            events=(MasteryEvent(delta=62, source="quiz:a1", occurred_at=T0),),
        )
    )
    attempts.add_quiz_attempt(
        "u1",
        QuizAttempt(stable_id="a1", quiz_id="q1", score=80, is_best=True, attempted_at=T0),
        (Answer(question_id="qq1", first_answered_at=T0, counted_for_mastery=True),),
    )
    progress.upsert_module_progress(
        ModuleProgressRecord(enrollment_id="e1", module_id="m1", state="in_progress")
    )
    progress.add_lesson_completion(
        LessonCompletionRecord(module_id="m1", lesson_id="l1", completed_at=T0)
    )
    return mastery, attempts, progress


def _snapshot(
    mastery: In_Memory_MasteryRepository,
    attempts: In_Memory_AttemptRepository,
    progress: In_Memory_ProgressRepository,
) -> tuple[object, ...]:
    return (
        mastery.list_for_user("u1"),
        mastery.get("u1", "c1"),
        attempts.list_quiz_attempts("u1", "q1"),
        attempts.list_answers_for_question("u1", "qq1"),
        progress.list_module_progress("e1"),
        progress.list_lesson_completions("m1"),
    )


# --- provisioning rerun after a crash (Req 20.5, 20.7) ------------------------------


# Claims: 20.5, 20.7
def test_provisioning_is_rerun_after_a_crash_mid_attempt_and_reaches_ready() -> None:
    env = _Env()
    env.add_provisioning()
    # The previous worker claimed the attempt, then died before recording a result.
    assert env.sessions.claim_provision_attempt("s1", expected_attempts=0, now=env.clock.now())
    assert env.get().provision_attempts == 1
    assert env.get().status is SessionStatus.PROVISIONING

    # The queue redelivers the job to the restarted worker.
    outcome = env.provisioner.execute("s1")

    assert outcome is ProvisionOutcome.READY
    session = env.get()
    assert session.status is SessionStatus.READY
    assert session.sandbox_ref is not None
    assert session.provision_attempts == 2


# Claims: 20.5, 20.7
def test_crash_after_the_last_allowed_attempt_fails_the_session_without_a_new_sandbox() -> None:
    env = _Env()
    env.add_provisioning()
    max_attempts = config.LAB_PROVISION_MAX_RETRIES + 1
    for n in range(max_attempts):
        assert env.sessions.claim_provision_attempt("s1", expected_attempts=n, now=env.clock.now())
    env.sandbox.calls.clear()

    assert env.provisioner.execute("s1") is ProvisionOutcome.FAILED

    assert env.get().status is SessionStatus.FAILED
    assert "provision" not in env.sandbox.calls


# Claims: 20.5, 20.7
def test_redelivery_of_a_finished_provision_job_touches_nothing() -> None:
    env = _Env()
    env.add_provisioning()
    assert env.provisioner.execute("s1") is ProvisionOutcome.READY
    ref = env.get().sandbox_ref
    env.sandbox.calls.clear()

    assert env.provisioner.execute("s1") is ProvisionOutcome.SKIPPED

    assert env.sandbox.calls == []
    assert env.get().sandbox_ref == ref and env.get().status is SessionStatus.READY


# Claims: 20.5, 20.7
def test_failed_attempt_is_retried_by_the_next_delivery_after_the_min_interval() -> None:
    env = _Env()
    env.add_provisioning()
    env.sandbox.script_provision(ProvisionMode.FAIL)

    assert env.provisioner.execute("s1") is ProvisionOutcome.RETRY_SCHEDULED
    assert env.provisioner.execute("s1") is ProvisionOutcome.SKIPPED  # not yet due

    env.clock.advance(config.LAB_PROVISION_MIN_INTERVAL)
    assert env.provisioner.execute("s1") is ProvisionOutcome.READY


# --- grading run interrupted by a restart (Req 20.6) --------------------------------


def _worker(
    env: _Env, mastery: In_Memory_MasteryRepository | None = None, **kw: object
) -> LabWorker:
    return build_lab_worker(
        queue=In_Memory_TaskQueue(),
        sessions=env.sessions,
        runs=env.runs,
        mastery=mastery,  # type: ignore[arg-type]
        catalog=Fake_LabCatalog([LAB]),
        clock=env.clock,
        bundle_root=Path("."),
        sandbox=env.sandbox,
        **kw,  # type: ignore[arg-type]
    )


def _grading_session(env: _Env, sid: str = "s1", user: str = "u1") -> None:
    env.add_provisioning(sid, user)
    env.provisioner.execute(sid)
    env.sessions.transition(sid, expected={SessionStatus.READY}, to=SessionStatus.GRADING)
    env.runs.add(GradingRun(f"r-{sid}", sid, RunStatus.RUNNING, T0, T0 + timedelta(seconds=15)))


# Claims: 20.6
def test_interrupted_run_is_recorded_failed_and_gives_no_mastery() -> None:
    env = _Env()
    mastery, attempts, progress = _learner_data()
    before = _snapshot(mastery, attempts, progress)
    _grading_session(env)
    worker = _worker(env, mastery)

    worker.start()
    try:
        run = env.runs.get("r-s1")
        assert run is not None
        assert run.status is RunStatus.COULD_NOT_COMPLETE
        assert run.results == ()  # nothing re-run, no raw output
        assert env.get().status is SessionStatus.READY  # the learner may grade again
    finally:
        worker.stop()
    assert _snapshot(mastery, attempts, progress) == before


# Claims: 20.6
def test_a_second_restart_leaves_the_already_closed_run_alone() -> None:
    env = _Env()
    _grading_session(env)
    first = _worker(env)
    first.start()
    first.stop()
    closed = env.runs.get("r-s1")

    second = _worker(env)
    second.start()
    second.stop()

    assert env.runs.get("r-s1") == closed


# Claims: 19.2, 20.6
def test_run_interrupted_on_a_session_lost_to_infrastructure_stays_ended() -> None:
    env = _Env()
    _grading_session(env)
    assert env.end.execute("s1") is True
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    worker = _worker(env)

    worker.start()
    worker.stop()

    run = env.runs.get("r-s1")
    assert run is not None and run.status is not RunStatus.RUNNING
    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE


# --- reaper resume (Req 20.7) ---------------------------------------------------------


# Claims: 20.7, 19.4
def test_reaper_resumes_after_restart_and_finishes_what_the_dead_one_started() -> None:
    env = _Env()
    for sid, user in (("s1", "u1"), ("s2", "u2")):
        env.add_provisioning(sid, user)
        env.provisioner.execute(sid)
    env.sandbox.vanish(env.handle("s1"))
    env.watcher.failed.add(env.handle("s2").sandbox_id)

    first = env.reaper().execute()  # the reaper that "crashed" after one tick
    assert first.infra_lost == 2

    # A fresh instance (worker restarted) neither repeats nor loses the work.
    again = env.reaper().execute()
    assert again.infra_lost == 0
    assert env.get("s1").status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.get("s2").status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sandbox.sandbox(env.handle("s2")).destroyed


# Claims: 20.7
def test_reaper_after_restart_leaves_healthy_sessions_and_their_pods_alone() -> None:
    env = _Env()
    env.add_provisioning()
    env.provisioner.execute("s1")

    env.clock.advance(timedelta(seconds=30))
    report = env.reaper().execute()

    assert report.infra_lost == 0
    assert env.get().status is SessionStatus.READY
    assert not env.sandbox.sandbox(env.handle()).destroyed


# --- evicted pod removal (Req 19.4) -----------------------------------------------------


# Claims: 19.2, 19.4
def test_evicted_pod_event_ends_the_session_and_removes_the_pod() -> None:
    env = _Env()
    env.add_provisioning()
    env.provisioner.execute("s1")
    handle = env.handle()
    job = WatchLabPods(
        watcher=env.watcher,
        end=env.end,
        sandbox=env.sandbox,
        metrics=env.metrics,
        sleep=lambda _s: None,
    )

    job.handle(LabPodEvent(handle, PodLossKind.EVICTED))

    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sandbox.sandbox(handle).destroyed


# Claims: 19.4
def test_evicted_pod_missed_by_the_watch_is_removed_by_the_reaper() -> None:
    env = _Env()
    env.add_provisioning()
    env.provisioner.execute("s1")
    env.watcher.failed.add(env.handle().sandbox_id)

    env.reaper().execute()

    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sandbox.sandbox(env.handle()).destroyed


# --- learner progress unchanged (Req 19.2, 19.4, 20.6) ------------------------------------


# Claims: 19.2, 19.4
def test_infrastructure_loss_leaves_progress_attempts_and_mastery_unchanged() -> None:
    env = _Env()
    mastery, attempts, progress = _learner_data()
    before = _snapshot(mastery, attempts, progress)
    for sid, user in (("s1", "u1"), ("s2", "u1")):
        env.add_provisioning(sid, user)
        env.provisioner.execute(sid)
        if sid == "s1":
            env.sessions.transition(sid, expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    # Pod events and reaper detection of the live session's pod loss.
    job = WatchLabPods(
        watcher=env.watcher,
        end=env.end,
        sandbox=env.sandbox,
        metrics=env.metrics,
        sleep=lambda _s: None,
    )
    job.handle(LabPodEvent(env.handle("s2"), PodLossKind.EVICTED))
    env.reaper().execute()

    assert env.get("s2").status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert _snapshot(mastery, attempts, progress) == before
    assert len(mastery.get("u1", "c1").events) == 1  # type: ignore[union-attr]


# Claims: 19.2, 19.4, 20.6
def test_learner_can_start_again_and_data_is_unchanged_after_loss_and_restart() -> None:
    env = _Env()
    mastery, attempts, progress = _learner_data()
    before = _snapshot(mastery, attempts, progress)
    _grading_session(env)
    env.sandbox.vanish(env.handle())

    worker = _worker(env, mastery)
    worker.start()
    worker.stop()
    env.reaper().execute()

    assert env.get().status is SessionStatus.ENDED_BY_INFRASTRUCTURE
    assert env.sessions.get_active_for_user("u1") is None  # no cooldown: free to retry
    assert _snapshot(mastery, attempts, progress) == before
