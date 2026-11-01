"""WebSocket failure scenarios for the terminal (task 10.9).

Covers what the route and gateway unit tests do not: ownership re-verified on
every reconnect, foreign refusal on connect and reconnect, and a session that
ended while the socket was down.

**Validates: Requirements 12.12, 13.2, 26.1, 26.2, 26.4, 26.5, 26.6, 26.7**
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_application.labs import (
    AuthorizeTerminal,
    Frame,
    TerminalAuthorization,
    TerminalGatewayCore,
)
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)

_PATH = Path(__file__).with_name("test_labs_terminal_route.py")
_SPEC = importlib.util.spec_from_file_location("_labs_terminal_route_env", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
_Setup: Any = _MODULE._Setup
UNKNOWN: str = _MODULE.UNKNOWN
ORIGIN = config.LAB_ALLOWED_WEB_ORIGINS[0]


def _run(scenario: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(scenario())


class _Socket:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.sent: list[Frame] = []
        self.closed: tuple[int, str] | None = None

    async def receive(self) -> Frame | None:
        return await self.inbox.get()

    async def send(self, frame: Frame) -> None:
        self.sent.append(frame)

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)

    def texts(self) -> list[dict[str, Any]]:
        return [json.loads(f.text) for f in self.sent if f.text]


def test_reconnect_by_owner_is_accepted_and_foreign_reconnect_is_refused() -> None:
    async def scenario() -> None:
        setup = _Setup()
        first = setup.connect()
        assert (await first.open())["type"] == "websocket.accept"
        await first.disconnect()
        # Reconnect is a new connection that runs every check again.
        again = setup.connect()
        assert (await again.open())["type"] == "websocket.accept"
        await again.disconnect()
        unknown = await setup.refusal(path=UNKNOWN)
        assert await setup.refusal(ref="ref-u2") == unknown  # foreign, first connect
        assert await setup.refusal(ref="ref-u2") == unknown  # foreign, reconnect

    _run(scenario)


def test_reconnect_after_session_ended_while_down_is_refused_like_unknown() -> None:
    async def scenario() -> None:
        setup = _Setup()
        first = setup.connect()
        assert (await first.open())["type"] == "websocket.accept"
        await first.disconnect()
        setup.env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
        unknown = await setup.refusal(path=UNKNOWN)
        assert await setup.refusal() == unknown

    _run(scenario)


class _Core:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.store = In_Memory_SessionStore(self.clock)
        for ref, user in (("ref-u1", "u1"), ("ref-u2", "u2")):
            self.store.create(ref, user, expires_at=self.clock.now() + timedelta(days=1))
        lab = LabDefinition(
            lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
        )
        self.handle = self.sandbox.provision(build_sandbox_spec(lab, "s1"))
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id="s1",
                user_id="u1",
                lab_id="lab1",
                status=SessionStatus.READY,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
                sandbox_ref=self.handle.sandbox_id,
            )
        )
        self.auth = AuthorizeTerminal(sessions=self.sessions, session_store=self.store)

    def gateway(self) -> TerminalGatewayCore:
        return TerminalGatewayCore(
            sessions=self.sessions, sandbox=self.sandbox, clock=self.clock, provider_name="fake"
        )


def test_gateway_reverifies_ownership_on_every_run() -> None:
    async def scenario() -> None:
        core = _Core()
        gw = core.gateway()
        first = _Socket()
        task = asyncio.create_task(gw.run(TerminalAuthorization("s1", "u1"), first))
        await asyncio.sleep(0.05)
        await first.inbox.put(None)
        assert await asyncio.wait_for(task, 2) == 1000
        # A reconnect carrying someone else's authorization is refused like a missing session.
        foreign, missing = _Socket(), _Socket()
        assert await gw.run(TerminalAuthorization("s1", "u2"), foreign) == 4404
        assert await gw.run(TerminalAuthorization("nope", "u2"), missing) == 4404
        assert foreign.closed == missing.closed
        assert not foreign.sent

    _run(scenario)


def test_session_ended_while_connected_sends_status_frame_before_close() -> None:
    async def scenario() -> None:
        core = _Core()
        sock = _Socket()
        task = asyncio.create_task(core.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await asyncio.sleep(0.05)
        core.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
        core.sandbox.destroy(core.handle)
        assert await asyncio.wait_for(task, 5) == 1000
        status = [t for t in sock.texts() if t["type"] == "status"]
        assert status and status[0]["status"] == "ended"
        assert status[0]["message_key"] == "labs.terminal.status.ended"
        assert sock.closed is not None and sock.closed[0] == 1000

    _run(scenario)
