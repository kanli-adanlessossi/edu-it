"""Route tests for the cursor-paginated list endpoints (task 18.2, Req 17.5).

**Validates: Requirements 17.5**
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from edu_it_api.errors.handlers import install_error_handlers
from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_api.learning_lists import ListServices, build_list_router
from edu_it_api.versioning import build_version_app
from edu_it_application.learning.pagination import (
    CursorCodec,
    ListEnrollmentsUseCase,
    ListQuizAttemptsUseCase,
)
from edu_it_domain.ports.enrollment import EnrollmentRecord
from edu_it_infrastructure.fakes import (
    In_Memory_AttemptRepository,
    In_Memory_EnrollmentRepository,
    Test_Clock,
)
from fastapi import FastAPI

_T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _app() -> FastAPI:
    clock = Test_Clock(start=_T0)
    codec = CursorCodec(clock)
    enrollments = In_Memory_EnrollmentRepository()
    for i in range(3):
        enrollments.add(
            EnrollmentRecord(
                stable_id=f"enr-{i}",
                user_id="usr-1",
                track_id=f"trk-{i}",
                enrolled_at=_T0 + timedelta(minutes=i),
            )
        )
    services = ListServices(
        current_user=lambda _r: "usr-1",
        list_enrollments=ListEnrollmentsUseCase(enrollments=enrollments, codec=codec),
        list_quiz_attempts=ListQuizAttemptsUseCase(
            attempts=In_Memory_AttemptRepository(), codec=codec
        ),
    )
    app = build_version_app(1, [build_list_router(services)])
    install_error_handlers(app)
    install_learning_error_handlers(app)
    return app


def _get(app: FastAPI, path: str, query: str = "") -> tuple[int, str, Any]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    ctype = dict(start["headers"]).get(b"content-type", b"").decode()
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], ctype, json.loads(payload)


def test_enrollment_pages_chain_through_next_cursor() -> None:
    app = _app()
    _, _, first = _get(app, "/enrollments", "limit=2")
    assert [i["stable_id"] for i in first["items"]] == ["enr-0", "enr-1"]
    _, _, second = _get(app, "/enrollments", f"limit=2&cursor={first['next_cursor']}")
    assert [i["stable_id"] for i in second["items"]] == ["enr-2"]
    assert second["next_cursor"] is None


def test_malformed_cursor_returns_400_problem() -> None:
    status, ctype, body = _get(_app(), "/enrollments", "cursor=garbage")
    assert status == 400
    assert ctype.startswith("application/problem+json")
    assert body["status"] == 400
    assert body["type"].endswith("invalid-cursor")
