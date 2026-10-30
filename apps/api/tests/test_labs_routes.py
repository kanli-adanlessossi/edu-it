"""Example tests for the labs router under /v1 (task 7.3).

Drives the real router, use cases and domain rules over the infrastructure fakes
through a minimal in-process ASGI call (the project pins no HTTP client). The
router is mounted on a bare versioned sub-app with the error handlers installed.

**Validates: Requirements 6.4, 14.2, 14.7, 15.3, 17.1, 17.2, 17.3, 17.4, 17.5, 30.6**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from dataclasses import replace
from datetime import timedelta
from typing import Any

from edu_it_api.app import create_app
from edu_it_api.errors import install_error_handlers
from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.versioning import build_version_app
from edu_it_application.labs import (
    AuthorizeTerminal,
    EndSessionUseCase,
    GetSessionUseCase,
    ListSessionsUseCase,
    RequestGradingUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabUseCase,
    TerminalGatewayCore,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain import config
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_SessionStore,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)
from fastapi import FastAPI

_LAB = LabDefinition(
    lab_id="lab1",
    kind="code",
    image="img",
    concept_ids=("con-1",),
    tasks=(
        LabTaskDefinition(
            "t1", required=True, text={"en": "Do it", "fr": "Faites-le"}, hint_count=2
        ),
    ),
    checks=(),
    text={"en": "Lab one", "fr": "Lab un"},
    learner_files=("main.py",),
)
_HINTS = {1: "first hint", 2: "second hint"}


async def _no_sleep(_seconds: float) -> None:
    return None


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        runs = self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(runs)
        self.queue = In_Memory_TaskQueue()
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.session_store = In_Memory_SessionStore(self.clock)
        catalog = Fake_LabCatalog([_LAB])
        idem = In_Memory_IdempotencyStore()
        ids = Sequential_IdGenerator("ses")
        end = EndSessionUseCase(sessions=self.sessions, queue=self.queue, clock=self.clock)
        start = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=self.queue,
            idempotency=idem,
            clock=self.clock,
            ids=ids,
        )
        services = LabServices(
            current_user=lambda r: r.headers["x-user"],
            catalog=catalog,
            start=start,
            reset=ResetLabUseCase(sessions=self.sessions, end=end, start=start),
            end=end,
            get_session=GetSessionUseCase(sessions=self.sessions),
            list_sessions=ListSessionsUseCase(sessions=self.sessions),
            save_files=SaveFilesUseCase(
                sessions=self.sessions,
                catalog=catalog,
                sandbox=self.sandbox,
                clock=self.clock,
                provider_name="fake",
            ),
            reveal_hint=RevealLabHintUseCase(
                sessions=self.sessions,
                catalog=catalog,
                mastery=In_Memory_MasteryRepository(),
                clock=self.clock,
            ),
            hint_text=lambda _lab, _task, level, _loc: _HINTS.get(level),
            cursor_codec=CursorCodec(self.clock),
            clock=self.clock,
            request_grading=RequestGradingUseCase(
                sessions=self.sessions,
                runs=runs,
                queue=self.queue,
                idempotency=idem,
                clock=self.clock,
                ids=Sequential_IdGenerator("run"),
            ),
            runs=runs,
            feedback_messages=lambda _lab, _loc: {"fb.pass": "Well done"},
            sleep=_no_sleep,
            authorize_terminal=AuthorizeTerminal(
                sessions=self.sessions, session_store=self.session_store
            ),
            terminal_gateway=TerminalGatewayCore(
                sessions=self.sessions,
                sandbox=self.sandbox,
                clock=self.clock,
                provider_name="fake",
            ),
        )
        self.router = build_labs_router(services)
        self.app: FastAPI = build_version_app(1, [self.router])
        install_error_handlers(self.app)

    def call(
        self,
        method: str,
        path: str,
        *,
        user: str = "u1",
        body: Any = None,
        query: str = "",
        key: str | None = None,
        lang: str | None = None,
    ) -> tuple[int, Any]:
        headers = {"x-user": user}
        if key is not None:
            headers["idempotency-key"] = key
        if lang is not None:
            headers["accept-language"] = lang
        return _call(self.app, method, path, body=body, query=query, headers=headers)

    def start(self, key: str = "k1", user: str = "u1") -> dict[str, Any]:
        status, data = self.call(
            "POST", "/lab-sessions", user=user, body={"lab_id": "lab1"}, key=key
        )
        assert status == 201, data
        return data  # type: ignore[no-any-return]

    def make_ready(self, session_id: str) -> None:
        handle = self.sandbox.provision(_spec(session_id))
        assert self.sessions.transition(
            session_id,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.READY,
            sandbox_ref=handle.sandbox_id,
        )


def _spec(sid: str):  # type: ignore[no-untyped-def]
    from edu_it_domain.labs.models import SandboxSpec

    return SandboxSpec(sid, "img", "learner", False, 1, 1, 1, 1, True, True, True, (), ())


def _call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"content-type", b"application/json")]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": hdrs,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload) if payload else None


def _int_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _int_values(v)]
    if isinstance(value, list):
        return [x for v in value for x in _int_values(v)]
    return [value] if isinstance(value, int) and not isinstance(value, bool) else []


# --- labs ---------------------------------------------------------------------


def test_get_lab_localized_withholds_hint_text() -> None:
    env = _Env()
    status, data = env.call("GET", "/labs/lab1", query="locale=fr")
    assert status == 200
    assert data["title"] == "Lab un"
    assert data["tasks"] == [
        {"task_id": "t1", "required": True, "text": "Faites-le", "hint_count": 2}
    ]
    assert data["learner_files"] == ["main.py"]
    assert "hint" not in json.dumps(data).replace("hint_count", "")


def test_get_unknown_lab_is_404_problem() -> None:
    status, data = _Env().call("GET", "/labs/nope")
    assert status == 404
    assert data["status"] == 404


# --- start / get --------------------------------------------------------------


def test_start_returns_201_provisioning_with_deadlines() -> None:
    env = _Env()
    data = env.start()
    assert data["status"] == "provisioning"
    assert data["lab_id"] == "lab1"
    assert data["error"] is None
    assert data["grading_available_at"] is None
    assert data["ttl_at"] > data["server_now"]
    assert data["idle_deadline"] > data["server_now"]


def test_start_requires_idempotency_key() -> None:
    status, _ = _Env().call("POST", "/lab-sessions", body={"lab_id": "lab1"})
    assert status == 422


def test_start_replays_same_key_and_conflicts_on_second_active_session() -> None:
    env = _Env()
    first = env.start("k1")
    assert env.start("k1")["stable_id"] == first["stable_id"]
    status, data = env.call("POST", "/lab-sessions", body={"lab_id": "lab1"}, key="k2")
    assert status == 409
    assert data["status"] == 409


def test_get_session_and_foreign_user_gets_404() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    status, data = env.call("GET", f"/lab-sessions/{sid}")
    assert status == 200
    assert data["stable_id"] == sid
    status, _ = env.call("GET", f"/lab-sessions/{sid}", user="u2")
    assert status == 404
    status, _ = env.call("GET", "/lab-sessions/missing")
    assert status == 404


def test_grading_available_at_follows_last_accepted_request() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    stored = env.sessions.get(sid)
    assert stored is not None
    env.sessions._sessions[sid] = replace(stored, last_grading_accepted_at=env.clock.now())
    _, data = env.call("GET", f"/lab-sessions/{sid}")
    expected = (
        (env.clock.now() + config.LAB_GRADING_MIN_INTERVAL).isoformat().replace("+00:00", "Z")
    )
    assert data["grading_available_at"] == expected


def test_failed_session_carries_translated_error() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    env.sessions.transition(sid, expected={SessionStatus.PROVISIONING}, to=SessionStatus.FAILED)
    _, en = env.call("GET", f"/lab-sessions/{sid}", lang="en")
    _, fr = env.call("GET", f"/lab-sessions/{sid}", lang="fr")
    assert en["status"] == "failed"
    assert en["error"]["title"] == "The lab could not start"
    assert fr["error"]["title"] == "Le lab n'a pas pu démarrer"


# --- list ---------------------------------------------------------------------


def test_list_sessions_cursor_pagination_without_internal_keys() -> None:
    env = _Env()
    ids = []
    for i in range(3):
        sid = env.start(f"k{i}")["stable_id"]
        ids.append(sid)
        env.clock.advance(timedelta(seconds=1))
        assert env.call("DELETE", f"/lab-sessions/{sid}")[0] == 200
    status, page1 = env.call("GET", "/lab-sessions", query="limit=2")
    assert status == 200
    assert [s["stable_id"] for s in page1["items"]] == ids[:2]
    assert page1["next_cursor"] is not None
    _, page2 = env.call("GET", "/lab-sessions", query=f"limit=2&cursor={page1['next_cursor']}")
    assert [s["stable_id"] for s in page2["items"]] == ids[2:]
    assert page2["next_cursor"] is None
    status, _ = env.call("GET", "/lab-sessions", query="cursor=garbage")
    assert status == 400


# --- end / reset ----------------------------------------------------------------


def test_delete_ends_session_idempotently() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    for _ in range(2):
        status, data = env.call("DELETE", f"/lab-sessions/{sid}")
        assert status == 200
        assert data["status"] == "ended"
    assert env.call("DELETE", f"/lab-sessions/{sid}", user="u2")[0] == 404


def test_reset_ends_old_and_starts_fresh_session_of_same_lab() -> None:
    env = _Env()
    old = env.start()["stable_id"]
    status, data = env.call("POST", f"/lab-sessions/{old}/reset", key="r1")
    assert status == 201
    assert data["stable_id"] != old
    assert data["lab_id"] == "lab1"
    assert data["status"] == "provisioning"
    _, old_view = env.call("GET", f"/lab-sessions/{old}")
    assert old_view["status"] == "ended"


# --- files ----------------------------------------------------------------------


def test_put_files_saves_and_rejects_invalid_names() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    status, _ = env.call("PUT", f"/lab-sessions/{sid}/files", body={"files": {"main.py": "x"}})
    assert status == 409  # still provisioning: nothing to write to
    env.make_ready(sid)
    status, data = env.call("PUT", f"/lab-sessions/{sid}/files", body={"files": {"main.py": "x"}})
    assert (status, data) == (200, {"saved": 1})
    status, data = env.call("PUT", f"/lab-sessions/{sid}/files", body={"files": {"evil.sh": "x"}})
    assert status == 422
    assert data["status"] == 422


# --- hints ----------------------------------------------------------------------


def test_hints_are_progressive_and_text_is_served_once_granted() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    path = f"/lab-sessions/{sid}/hints"
    status, _ = env.call("POST", path, body={"task_id": "t1", "level": 2})
    assert status == 409
    status, data = env.call("POST", path, body={"task_id": "t1", "level": 1})
    assert status == 200
    assert data == {
        "task_id": "t1",
        "level": 1,
        "total_levels": 2,
        "newly_revealed": True,
        "text": "first hint",
    }
    _, again = env.call("POST", path, body={"task_id": "t1", "level": 1})
    assert again["newly_revealed"] is False
    assert env.call("POST", path, body={"task_id": "t1", "level": 2})[1]["text"] == "second hint"
    assert env.call("POST", path, body={"task_id": "t1", "level": 3})[0] == 404
    assert env.call("POST", path, body={"task_id": "zz", "level": 1})[0] == 404


# --- no internal keys / versioning ----------------------------------------------


def test_routes_registered_under_v1_only_and_bodies_carry_no_numeric_keys() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    root = create_app(lab_router=env.router)
    status, doc = _call(root, "GET", "/v1/openapi.json")
    assert status == 200
    assert {"/labs/{lab_id}", "/lab-sessions", "/lab-sessions/{session_id}"} <= set(doc["paths"])
    assert _call(root, "GET", "/labs/lab1", headers={"x-user": "u1"})[0] == 404
    bodies = [
        env.call("GET", f"/lab-sessions/{sid}")[1],
        env.call("GET", "/lab-sessions")[1],
        env.call("GET", "/labs/lab1")[1],
        env.call("POST", f"/lab-sessions/{sid}/hints", body={"task_id": "t1", "level": 1})[1],
    ]
    # Counts and 1-based hint levels are the only integers; none is a row key.
    ints = [i for b in bodies for i in _int_values(b)]
    assert all(0 <= i <= 2 for i in ints)
    assert not any(isinstance(v, int) and v > 1000 for v in ints)
