"""Example tests for lab worker behavior, driven through the worker job handlers (task 6.18).

Jobs are enqueued on the in-memory queue and delivered to the consumers assembled by
``build_lab_worker``, so the handler adapters and the real use cases run together on the
fakes. Scheduler tick dedupe is covered in ``test_lab_worker_wiring.py``.

**Validates: Requirements 6.3, 6.6, 8.5, 25.5**
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckOutcome,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_TaskQueue,
    Test_Clock,
)
from edu_it_worker.labs import build_lab_worker
from edu_it_worker.labs.jobs import DESTROY_JOB, GRADE_JOB, PROVISION_JOB, REAP_JOB

_LAB = LabDefinition(
    lab_id="lab1",
    kind="terminal",
    image="img",
    concept_ids=("cn1",),
    tasks=(LabTaskDefinition("t1", required=True),),
    checks=(
        LabCheckDefinition("c1", "t1", 5.0),
        LabCheckDefinition("c2", "t1", 5.0),
    ),
    bundle_path="lab1",
)


class _Env:
    def __init__(self, tmp_path: Path) -> None:
        (tmp_path / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (tmp_path / "lab1" / "expected").mkdir()
        self.script = tmp_path / "setup.sh"
        self.script.write_text("echo hi\n", encoding="utf-8")
        self.clock = Test_Clock()
        self.queue = In_Memory_TaskQueue()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.worker = build_lab_worker(
            queue=self.queue,
            sessions=self.sessions,
            runs=self.runs,
            mastery=In_Memory_MasteryRepository(),
            catalog=Fake_LabCatalog([_LAB]),
            clock=self.clock,
            bundle_root=tmp_path,
            settings=CompositionSettings(),
            sandbox=self.sandbox,
            setup_scripts=lambda _lab: self.script,
        )
        for name, handler in self.worker.consumers.items():
            self.queue.consume(name, handler)

    def add_session(self, status: SessionStatus, **fields: object) -> LabSession:
        now = self.clock.now()
        session = LabSession(
            stable_id="s1",
            user_id="u1",
            lab_id="lab1",
            status=status,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            **fields,  # type: ignore[arg-type]
        )
        self.sessions.add_if_no_active(session)
        return session

    def session(self) -> LabSession:
        got = self.sessions.get("s1")
        assert got is not None
        return got

    def live_sandbox(self) -> SandboxHandle:
        """Provision a sandbox directly and attach it to the session."""
        handle = self.sandbox.provision(build_sandbox_spec(_LAB, "s1"))
        self.sessions.transition(
            "s1",
            expected={self.session().status},
            to=self.session().status,
            sandbox_ref=handle.sandbox_id,
            last_activity_at=self.clock.now(),
        )
        return handle

    def deliver(self, job: str, payload: dict[str, str]) -> None:
        self.queue.enqueue(job, payload)
        self.queue.run_pending()


def test_setup_script_runs_before_session_becomes_ready(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.add_session(SessionStatus.PROVISIONING)
    statuses_at_setup: list[SessionStatus] = []

    def handler(_sb, command, _user):  # type: ignore[no-untyped-def]
        if "/bin/bash" in command:
            statuses_at_setup.append(env.session().status)
        return None

    env.sandbox.exec_handler = handler
    env.deliver(PROVISION_JOB, {"session_id": "s1"})

    assert statuses_at_setup == [SessionStatus.PROVISIONING]
    assert env.session().status is SessionStatus.READY
    assert env.session().sandbox_ref is not None


def test_failed_setup_script_never_reaches_ready_and_leaves_no_sandbox(tmp_path: Path) -> None:
    from edu_it_domain.ports.sandbox import ExecResult

    env = _Env(tmp_path)
    env.add_session(SessionStatus.PROVISIONING)
    env.sandbox.exec_handler = lambda _sb, cmd, _u: (
        ExecResult(3, b"", b"", False) if "/bin/bash" in cmd else None
    )
    env.deliver(PROVISION_JOB, {"session_id": "s1"})

    assert env.session().status is SessionStatus.PROVISIONING  # retry scheduled
    assert env.session().next_provision_at is not None
    assert env.sandbox.list_sandboxes() == ()


def test_duplicate_provision_delivery_provisions_once(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.add_session(SessionStatus.PROVISIONING)
    # Two deliveries that bypass queue dedupe, as an at-least-once broker can produce.
    env.deliver(PROVISION_JOB, {"session_id": "s1"})
    env.deliver(PROVISION_JOB, {"session_id": "s1"})

    assert env.sandbox.calls.count("provision") == 1
    assert len(env.sandbox.list_sandboxes()) == 1
    assert env.session().status is SessionStatus.READY
    assert env.session().provision_attempts == 1


def _start_grading(env: _Env) -> None:
    env.add_session(SessionStatus.GRADING)
    handle = env.live_sandbox()
    env.sandbox.add_process(handle, "learner", "bash")
    now = env.clock.now()
    env.runs.add(
        GradingRun("r1", "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
    )


def test_duplicate_grade_delivery_appends_results_once(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    _start_grading(env)
    payload = {"run_id": "r1", "session_id": "s1"}

    env.deliver(GRADE_JOB, payload)
    first = env.runs.get("r1")
    assert first is not None and first.status is RunStatus.PASSED
    assert [r.check_id for r in first.results] == ["c1", "c2"]

    env.deliver(GRADE_JOB, payload)  # duplicate after completion: nothing to grade
    assert env.runs.get("r1") == first


def test_regrade_after_crash_does_not_duplicate_results(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    _start_grading(env)
    payload = {"run_id": "r1", "session_id": "s1"}
    env.deliver(GRADE_JOB, payload)

    # Simulate a worker that died after recording results but before finishing the run.
    done = env.runs.get("r1")
    assert done is not None
    env.runs._runs["r1"] = replace(done, status=RunStatus.RUNNING, graded_at=None)
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.GRADING)
    env.deliver(GRADE_JOB, payload)

    again = env.runs.get("r1")
    assert again is not None
    assert [r.check_id for r in again.results] == ["c1", "c2"]
    assert all(r.outcome is CheckOutcome.PASSED for r in again.results)
    assert again.status is RunStatus.PASSED
    assert env.session().status is SessionStatus.READY


def test_reaper_logs_failed_destroy_and_retries_next_tick(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = _Env(tmp_path)
    env.add_session(SessionStatus.PROVISIONING)
    env.deliver(PROVISION_JOB, {"session_id": "s1"})
    assert env.session().status is SessionStatus.READY

    real_destroy = env.sandbox.destroy
    # The expiry destroy and the same tick's orphan sweep both fail.
    failures = {"left": 2}

    def flaky_destroy(handle: SandboxHandle) -> None:
        if failures["left"] > 0:
            failures["left"] -= 1
            raise RuntimeError("docker daemon unavailable")
        real_destroy(handle)

    env.sandbox.destroy = flaky_destroy  # type: ignore[method-assign]
    env.clock.advance(config.LAB_SESSION_TTL + timedelta(seconds=1))

    with caplog.at_level(logging.WARNING):
        env.deliver(REAP_JOB, {})
    assert env.session().status is SessionStatus.EXPIRED
    assert len(env.sandbox.list_sandboxes()) == 1  # destroy failed, sandbox remains
    assert any("destroying sandbox" in r.getMessage() for r in caplog.records)

    env.deliver(REAP_JOB, {})  # next tick destroys the orphan
    assert env.sandbox.list_sandboxes() == ()


def test_failed_destroy_job_is_retried_by_reaper(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    env.add_session(SessionStatus.PROVISIONING)
    env.deliver(PROVISION_JOB, {"session_id": "s1"})
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    real_destroy = env.sandbox.destroy

    def boom(_handle: SandboxHandle) -> None:
        raise RuntimeError("down")

    env.sandbox.destroy = boom  # type: ignore[method-assign]
    env.deliver(DESTROY_JOB, {"session_id": "s1"})
    assert len(env.sandbox.list_sandboxes()) == 1

    env.sandbox.destroy = real_destroy  # type: ignore[method-assign]
    env.deliver(REAP_JOB, {})
    assert env.sandbox.list_sandboxes() == ()


def test_reaper_closes_stale_running_run_and_thaws(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    _start_grading(env)
    handle = SandboxHandle(env.session().sandbox_ref or "", "s1", "fake")
    frozen = env.sandbox.add_process(handle, "learner", "sleep", state="T")
    env.clock.advance(config.LAB_GRADING_RUN_DEADLINE + timedelta(seconds=1))

    env.deliver(REAP_JOB, {})

    run = env.runs.get("r1")
    assert run is not None and run.status is RunStatus.COULD_NOT_COMPLETE
    assert run.graded_at == env.clock.now()
    assert frozen.state != "T"
    assert env.session().status is SessionStatus.READY
    assert env.sandbox.sandbox(handle).destroyed is False
