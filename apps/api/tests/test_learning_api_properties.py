"""Property tests for stable-id addressing and API-level cursor pagination (task 18.3).

**Validates: Requirements 17.4, 17.5**
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
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _app(minutes: list[int]) -> FastAPI:
    clock = Test_Clock(start=_T0)
    codec = CursorCodec(clock)
    enrollments = In_Memory_EnrollmentRepository()
    for i, m in enumerate(minutes):
        enrollments.add(
            EnrollmentRecord(
                stable_id=f"enr-{i:03d}",
                user_id="usr-1",
                track_id=f"trk-{i}",
                enrolled_at=_T0 + timedelta(minutes=m),
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


def _get(app: FastAPI, path: str, query: str = "") -> tuple[int, Any]:
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
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(payload)


def _id_fields(node: Any) -> list[tuple[str, Any]]:
    """Every (key, value) whose key names an identifier, found recursively."""
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "id" or key.endswith("_id") or key == "stable_id":
                found.append((key, value))
            found.extend(_id_fields(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_id_fields(item))
    return found


@settings(max_examples=50, deadline=None)
@given(
    minutes=st.lists(st.integers(min_value=0, max_value=30), min_size=1, max_size=12),
    limit=st.integers(min_value=1, max_value=5),
)
def test_responses_expose_only_stable_ids(minutes: list[int], limit: int) -> None:
    """Feature: learning-core, Property 27: Resources are addressed only by stable id.

    **Validates: Requirements 17.4**
    """
    app = _app(minutes)
    _, body = _get(app, "/enrollments", f"limit={limit}")
    assert body["items"]
    for item in body["items"]:
        assert "id" not in item
        assert isinstance(item["stable_id"], str)
        assert item["stable_id"].startswith("enr-")
    for key, value in _id_fields(body):
        assert key != "id"
        assert isinstance(value, str), f"{key} must be a stable string id, got {value!r}"


def test_openapi_has_no_numeric_id_properties_or_paths() -> None:
    """Feature: learning-core, Property 27: Resources are addressed only by stable id.

    **Validates: Requirements 17.4**
    """
    spec = _app([0]).openapi()
    for path in spec["paths"]:
        assert "{id}" not in path
    for schema in spec.get("components", {}).get("schemas", {}).values():
        for name, prop in schema.get("properties", {}).items():
            if name == "id" or name.endswith("_id") or name == "stable_id":
                assert prop.get("type") != "integer", f"{schema.get('title')}.{name}"
                assert name != "id"


@settings(max_examples=50, deadline=None)
@given(
    minutes=st.lists(st.integers(min_value=0, max_value=30), min_size=0, max_size=15),
    limit=st.integers(min_value=1, max_value=6),
)
def test_api_pages_cover_every_item_exactly_once(minutes: list[int], limit: int) -> None:
    """Feature: learning-core, Property 28: Cursor pagination covers the full set exactly once.

    **Validates: Requirements 17.5**
    """
    app = _app(minutes)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(len(minutes) + 2):
        query = f"limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        status, body = _get(app, "/enrollments", query)
        assert status == 200
        assert len(body["items"]) <= limit
        seen.extend(i["stable_id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    assert sorted(seen) == [f"enr-{i:03d}" for i in range(len(minutes))]
