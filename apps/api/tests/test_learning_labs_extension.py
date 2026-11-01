"""Progress and lesson endpoints expose lab data additively (hands-on-labs task 7.7).

**Validates: Requirements 15.12, 19.7**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.versioning import build_version_app
from edu_it_application.learning import GetLessonUseCase, GetProgressUseCase
from edu_it_domain.labs.models import TaskCompletion
from edu_it_domain.learning.models import Mastery, MasteryState
from edu_it_domain.ports.catalog import ModuleRecord
from edu_it_domain.ports.enrollment import EnrollmentRecord
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_domain.ports.lessons import LessonRecord
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_GradingRunRepository,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
)
from fastapi import FastAPI

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_TRACK = "trk-linux"
_LAB = "lab-perms"


def _call(app: FastAPI, method: str, path: str, *, query: str = "") -> tuple[int, Any]:
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


class _Lessons:
    def __init__(self, lab_ids: tuple[str, ...]) -> None:
        self._lesson = LessonRecord(
            stable_id="les-1",
            module_id="mod-1",
            titles={"en": "T"},
            bodies={"en": "b"},
            lab_ids=lab_ids,
        )

    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return self._lesson if stable_id == "les-1" else None


def _lab() -> LabDefinition:
    return LabDefinition(
        lab_id=_LAB,
        kind="terminal",
        image="img",
        concept_ids=("con-1", "con-2"),
        tasks=(
            LabTaskDefinition(task_id="t1", required=True),
            LabTaskDefinition(task_id="t2", required=True),
            LabTaskDefinition(task_id="t3", required=False),
        ),
        checks=(),
    )


def _app(*, with_labs: bool, done: tuple[str, ...] = (), lab_ids: tuple[str, ...] = ()) -> FastAPI:
    catalog = In_Memory_CatalogRepository()
    catalog.add_module(ModuleRecord(stable_id="mod-1", track_id=_TRACK, ordinal=1))
    enrollments = In_Memory_EnrollmentRepository()
    enrollments.add(
        EnrollmentRecord(stable_id="enr-1", user_id=_USER, track_id=_TRACK, enrolled_at=_T0)
    )
    mastery = In_Memory_MasteryRepository()
    mastery.save(
        Mastery(user_id=_USER, concept_id="con-1", score=40, state=MasteryState.INTRODUCED)
    )
    runs = In_Memory_GradingRunRepository()
    runs.add_task_completions(
        "run-1",
        [
            TaskCompletion(
                user_id=_USER, task_id=t, run_id="run-1", completed_at=_T0, counted_for_mastery=True
            )
            for t in done
        ],
    )
    progress = GetProgressUseCase(
        catalog=catalog,
        enrollments=enrollments,
        progress=In_Memory_ProgressRepository(),
        mastery=mastery,
        labs=Fake_LabCatalog([_lab()]) if with_labs else None,
        lab_completions=runs if with_labs else None,
    )
    services = LearningServices(
        current_user=lambda _r: _USER,
        enroll=None,  # type: ignore[arg-type]
        progress=progress,
        lessons=GetLessonUseCase(lessons=_Lessons(lab_ids)),
        attempt_quiz=None,  # type: ignore[arg-type]
        reveal_hint=None,  # type: ignore[arg-type]
        start_review=None,  # type: ignore[arg-type]
        submit_review=None,  # type: ignore[arg-type]
        submit_project=None,  # type: ignore[arg-type]
    )
    return build_version_app(1, [build_learning_router(services)])


def _progress(app: FastAPI) -> dict[str, Any]:
    status, body = _call(app, "GET", "/progress", query=f"track_id={_TRACK}")
    assert status == 200
    return body  # type: ignore[no-any-return]


def test_progress_labs_lists_completed_tasks_and_lab_mastery() -> None:
    body = _progress(_app(with_labs=True, done=("t1",)))
    (lab,) = body["labs"]
    assert lab["lab_id"] == _LAB
    assert lab["completed_task_ids"] == ["t1"]
    assert lab["passed"] is False
    assert [m["concept_id"] for m in lab["mastery"]] == ["con-1"]


def test_progress_labs_passed_when_all_required_tasks_done() -> None:
    body = _progress(_app(with_labs=True, done=("t1", "t2")))
    assert body["labs"][0]["passed"] is True


def test_progress_without_lab_ports_has_empty_labs_and_unchanged_fields() -> None:
    body = _progress(_app(with_labs=False))
    assert body["labs"] == []
    assert {"enrollment_id", "track_id", "modules", "mastery"} <= set(body)


def test_lesson_exposes_lab_ids() -> None:
    status, body = _call(_app(with_labs=False, lab_ids=(_LAB,)), "GET", "/lessons/les-1")
    assert (status, body["lab_ids"]) == (200, [_LAB])
    status, body = _call(_app(with_labs=False), "GET", "/lessons/les-1")
    assert (status, body["lab_ids"]) == (200, [])
