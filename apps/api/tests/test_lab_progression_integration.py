"""Integration tests for lab progression (hands-on-labs task 15.6).

Drives the real ``RecordCheckResult``, ``ReapSessions``, ``Content_LabCatalog``,
``Repository_LabCheck`` and the progress endpoint over in-memory fakes. Module "Processes"
requires both concepts covered by the "Files & Permissions" lab, as in the seed content.

**Validates: Requirements 19.6, 19.7, 27.6**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.versioning import build_version_app
from edu_it_application.labs import RecordCheckResult
from edu_it_application.labs.reap_sessions import ReapSessions
from edu_it_application.learning import GetLessonUseCase, GetProgressUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.learning.models import MasteryState
from edu_it_domain.ports.catalog import ModuleRecord
from edu_it_domain.ports.enrollment import EnrollmentRecord
from edu_it_domain.ports.progress import MODULE_STATE_LOCKED, MODULE_STATE_UNLOCKED
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.adapters.lab_check import Repository_LabCheck
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    Test_Clock,
)
from fastapi import FastAPI

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
USER = "usr-lea"
TRACK = "track.linux-fundamentals"
LAB = "lab.files-permissions"
READING = "concept.reading-permissions"
CHANGING = "concept.changing-permissions"
TASKS = ("task.1", "task.2", "task.3")


class _Store:
    """A content store exposing the synced lab items the catalog reads."""

    def __init__(self) -> None:
        def item(stable_id: str, payload: dict[str, Any]) -> SimpleNamespace:
            return SimpleNamespace(
                stable_id=stable_id, status="approved", payload=json.dumps(payload)
            )

        self._items = {
            "lab": [
                item(
                    LAB,
                    {
                        "kind": "terminal",
                        "image": "img",
                        "concept_stable_ids": [READING, CHANGING],
                        "check_bundle": "files-permissions",
                        "title": {"en": "Files & Permissions"},
                    },
                )
            ],
            "lab_task": [
                item(t, {"lab_stable_id": LAB, "ordinal": i, "required": True})
                for i, t in enumerate(TASKS)
            ],
            "lab_check": [
                item(f"check.{i}", {"lab_task_stable_id": t, "timeout_seconds": 5})
                for i, t in enumerate(TASKS)
            ],
        }

    def list_items(self, content_type: str) -> list[SimpleNamespace]:
        return self._items.get(content_type, [])


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock(T0)
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.mastery = In_Memory_MasteryRepository()
        self.labs = Content_LabCatalog(_Store())
        self.lab = self.labs.get(LAB)
        assert self.lab is not None
        self.lab_check = Repository_LabCheck(self.labs, self.runs, self.mastery)
        self.record = RecordCheckResult(runs=self.runs, mastery=self.mastery, clock=self.clock)
        self.session = LabSession(
            "s1", USER, LAB, SessionStatus.GRADING, T0, T0 + timedelta(minutes=10)
        )
        self.sessions.add_if_no_active(self.session)
        self.runs.add(GradingRun("run-1", "s1", RunStatus.RUNNING, T0, T0 + timedelta(seconds=15)))

        catalog = In_Memory_CatalogRepository()
        catalog.add_module(
            ModuleRecord(stable_id="module.files-permissions", track_id=TRACK, ordinal=0)
        )
        catalog.add_module(
            ModuleRecord(
                stable_id="module.processes",
                track_id=TRACK,
                ordinal=1,
                prerequisite_concept_ids=(READING, CHANGING),
            )
        )
        enrollments = In_Memory_EnrollmentRepository()
        enrollments.add(
            EnrollmentRecord(stable_id="enr-1", user_id=USER, track_id=TRACK, enrolled_at=T0)
        )
        progress = GetProgressUseCase(
            catalog=catalog,
            enrollments=enrollments,
            progress=In_Memory_ProgressRepository(),
            mastery=self.mastery,
            labs=self.labs,
            lab_completions=self.runs,
        )
        self.app = build_version_app(
            1,
            [
                build_learning_router(
                    LearningServices(
                        current_user=lambda _r: USER,
                        enroll=None,  # type: ignore[arg-type]
                        progress=progress,
                        lessons=GetLessonUseCase(lessons=None),  # type: ignore[arg-type]
                        attempt_quiz=None,  # type: ignore[arg-type]
                        reveal_hint=None,  # type: ignore[arg-type]
                        start_review=None,  # type: ignore[arg-type]
                        submit_review=None,  # type: ignore[arg-type]
                        submit_project=None,  # type: ignore[arg-type]
                    )
                )
            ],
        )

    def pass_task(self, index: int) -> None:
        assert self.lab is not None
        self.record.execute(
            "run-1",
            self.session,
            self.lab,
            CheckResult(f"check.{index}", TASKS[index], CheckOutcome.PASSED, "pass", "k"),
        )

    def progress(self) -> dict[str, Any]:
        status, body = _call(self.app, "GET", "/progress", f"track_id={TRACK}")
        assert status == 200
        return body  # type: ignore[no-any-return]

    def module_state(self, module_id: str) -> str:
        return next(m["state"] for m in self.progress()["modules"] if m["module_id"] == module_id)


def _call(app: FastAPI, method: str, path: str, query: str = "") -> tuple[int, Any]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload) if payload else None


@pytest.fixture
def env() -> _Env:
    return _Env()


def test_processes_unlocks_mid_lab_once_prerequisites_reach_the_required_state(env: _Env) -> None:
    assert env.module_state("module.processes") == MODULE_STATE_LOCKED
    env.pass_task(0)
    # One task (15 points per concept) is below Practiced (30): still locked.
    assert env.module_state("module.processes") == MODULE_STATE_LOCKED

    env.pass_task(1)  # second task: both prerequisite concepts reach Practiced
    mastery = {m.concept_id: m for m in env.mastery.list_for_user(USER)}
    assert {mastery[c].state for c in (READING, CHANGING)} == {MasteryState.PRACTICED}
    # The lab is not finished (task 3 outstanding), yet the dependent module is unlocked.
    assert not env.lab_check.passed(READING, USER)
    body = env.progress()
    assert env.module_state("module.processes") == MODULE_STATE_UNLOCKED
    assert body["labs"][0]["passed"] is False


def test_failed_and_could_not_complete_checks_never_unlock_or_lower_mastery(env: _Env) -> None:
    assert env.lab is not None
    env.pass_task(0)
    before = env.mastery.get(USER, READING)
    for outcome, code in (
        (CheckOutcome.FAILED, "fail"),
        (CheckOutcome.COULD_NOT_COMPLETE, "timeout"),
    ):
        env.record.execute(
            "run-1", env.session, env.lab, CheckResult("check.1", TASKS[1], outcome, code, "k")
        )
    assert env.mastery.get(USER, READING) == before
    assert env.module_state("module.processes") == MODULE_STATE_LOCKED


def test_expiry_keeps_completions_mastery_and_the_unlock(env: _Env) -> None:
    for i in (0, 1):
        env.pass_task(i)
    env.runs.finish("run-1", status=RunStatus.PASSED, graded_at=env.clock.now())
    env.sessions.transition("s1", expected={SessionStatus.GRADING}, to=SessionStatus.READY)

    sandbox = Fake_SandboxProvider(env.clock)
    handle = sandbox.provision(build_sandbox_spec(env.lab, "s1"))  # type: ignore[arg-type]
    env.sessions.transition(
        "s1", expected={SessionStatus.READY}, to=SessionStatus.READY, sandbox_ref=handle.sandbox_id
    )
    reaper = ReapSessions(
        sessions=env.sessions,
        runs=env.runs,
        sandbox=sandbox,
        clock=env.clock,
        provider_name=handle.provider,
    )
    env.clock.advance(timedelta(minutes=10, seconds=1))
    assert reaper.execute().expired == 1
    stored = env.sessions.get("s1")
    assert stored is not None and stored.status is SessionStatus.EXPIRED

    assert [c.task_id for c in env.runs.list_task_completions(USER)] == [TASKS[0], TASKS[1]]
    assert env.mastery.get(USER, READING).state is MasteryState.PRACTICED  # type: ignore[union-attr]
    assert env.module_state("module.processes") == MODULE_STATE_UNLOCKED


def test_progress_endpoint_returns_lab_completion_and_lab_derived_mastery(env: _Env) -> None:
    for i in (0, 1):
        env.pass_task(i)
    (lab,) = env.progress()["labs"]
    assert lab["lab_id"] == LAB
    assert lab["completed_task_ids"] == [TASKS[0], TASKS[1]]
    assert lab["passed"] is False
    assert {m["concept_id"] for m in lab["mastery"]} == {READING, CHANGING}
    assert all(m["score"] == 2 * config.LAB_TASK_MASTERY_GAIN for m in lab["mastery"])

    env.pass_task(2)
    (lab,) = env.progress()["labs"]
    assert lab["passed"] is True
    assert lab["completed_task_ids"] == list(TASKS)
    assert env.lab_check.passed(READING, USER)
