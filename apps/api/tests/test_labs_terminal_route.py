"""Tests for ``WS /v1/lab-sessions/{id}/terminal`` (task 10.6).

Drives the real app (CSRF and logging middleware included) through a minimal
in-process ASGI WebSocket client (the project pins no HTTP/WebSocket test
client) over the infrastructure fakes.

**Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.10, 12.11, 26.7**
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_api.app import create_app
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition

_SPEC = importlib.util.spec_from_file_location(
    "_labs_routes_env", Path(__file__).with_name("test_labs_routes.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
_Env: Any = _MODULE._Env

ORIGIN = config.LAB_ALLOWED_WEB_ORIGINS[0]
PATH = "/v1/lab-sessions/s1/terminal"
UNKNOWN = "/v1/lab-sessions/none/terminal"
_TIMEOUT = 5.0


class _Client:
    """One WebSocket connection to the ASGI app."""

    def __init__(self, app: Any, path: str, headers: dict[str, str]) -> None:
        self._to_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self._task = asyncio.create_task(app(scope, self._to_server.get, self._from_server.put))
        self._to_server.put_nowait({"type": "websocket.connect"})

    async def next(self) -> dict[str, Any]:
        return await asyncio.wait_for(self._from_server.get(), _TIMEOUT)

    async def open(self) -> dict[str, Any]:
        """First server message: ``accept`` when allowed, else a close/denial."""
        return await self.next()

    def send(self, *, data: bytes | None = None, text: str | None = None) -> None:
        self._to_server.put_nowait({"type": "websocket.receive", "bytes": data, "text": text})

    async def until(self, wanted: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        while True:
            msg = await self.next()
            if wanted(msg):
                return msg

    async def disconnect(self) -> None:
        self._to_server.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, _TIMEOUT)


class _Setup:
    def __init__(self, status: SessionStatus = SessionStatus.READY) -> None:
        env = self.env = _Env()
        now = env.clock.now()
        for ref, user in (("ref-u1", "u1"), ("ref-u2", "u2")):
            env.session_store.create(ref, user, expires_at=now + timedelta(days=1))
        lab = LabDefinition(
            lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
        )
        sandbox_id = env.sandbox.provision(build_sandbox_spec(lab, "s1")).sandbox_id
        env.sessions.add_if_no_active(
            LabSession(
                stable_id="s1",
                user_id="u1",
                lab_id="lab1",
                status=status,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
                sandbox_ref=sandbox_id,
            )
        )
        self.app = create_app(lab_router=env.router)

    def connect(
        self, *, ref: str | None = "ref-u1", origin: str | None = ORIGIN, path: str = PATH
    ) -> _Client:
        headers = {}
        if origin is not None:
            headers["origin"] = origin
        if ref is not None:
            headers["cookie"] = f"edu_it_session={ref}"
        return _Client(self.app, path, headers)

    async def refusal(self, **kw: Any) -> tuple[str, int | None, str]:
        client = self.connect(**kw)
        msg = await client.open()
        assert msg["type"] != "websocket.accept", "refused connect must not be accepted"
        await client._task
        return msg["type"], msg.get("code"), msg.get("reason", "")


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def test_owner_gets_binary_echo_and_json_control_frames() -> None:
    async def scenario() -> None:
        client = _Setup().connect()
        assert (await client.open())["type"] == "websocket.accept"
        client.send(data=b"ls\n")
        echoed = await client.until(lambda m: m["type"] == "websocket.send" and m.get("bytes"))
        assert echoed["bytes"] == b"ls\n"
        client.send(text=json.dumps({"type": "resize", "rows": 30, "cols": 100}))
        client.send(text='{"type":"ping"}')
        pong = await client.until(lambda m: m["type"] == "websocket.send" and m.get("text"))
        assert json.loads(pong["text"]) == {"type": "pong"}
        await client.disconnect()

    _run(scenario)


def test_refusals_follow_connect_order_and_steps_3_4_are_uniform() -> None:
    async def scenario() -> None:
        setup = _Setup()
        unauthenticated = await setup.refusal(ref=None, origin="https://evil.test", path=UNKNOWN)
        assert await setup.refusal(ref="unknown") == unauthenticated
        bad_origin = await setup.refusal(origin="https://evil.test", path=UNKNOWN)
        assert await setup.refusal(origin=None) == bad_origin
        unknown = await setup.refusal(path=UNKNOWN)
        foreign = await setup.refusal(ref="ref-u2")
        assert unknown == foreign
        assert len({unauthenticated, bad_origin, unknown}) == 3

    _run(scenario)


def test_ended_session_is_refused_like_unknown() -> None:
    async def scenario() -> None:
        setup = _Setup()
        unknown = await setup.refusal(path=UNKNOWN)
        setup.env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
        assert await setup.refusal() == unknown

    _run(scenario)


def test_second_connection_replaces_the_first() -> None:
    async def scenario() -> None:
        setup = _Setup()
        first = setup.connect()
        assert (await first.open())["type"] == "websocket.accept"
        first.send(data=b"a")
        await first.until(lambda m: m.get("bytes") == b"a")
        second = setup.connect()
        assert (await second.open())["type"] == "websocket.accept"
        closed = await first.until(lambda m: m["type"] == "websocket.close")
        assert closed["code"] == 4409
        await second.disconnect()

    _run(scenario)


def test_csrf_guard_still_rejects_unsafe_http_while_websocket_passes() -> None:
    async def scenario() -> None:
        setup = _Setup()
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/lab-sessions",
            "raw_path": b"/v1/lab-sessions",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 80),
        }
        await setup.app(scope, receive, send)
        assert sent[0]["status"] == 403

    _run(scenario)
