"""Unit tests for AuthorizeTerminal and TerminalGatewayCore (task 10.1)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import pytest
from edu_it_application.labs import (
    AuthorizeTerminal,
    Frame,
    TerminalAuthorization,
    TerminalGatewayCore,
    TerminalRefusal,
)
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_infrastructure.fakes import (
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_SessionStore,
    Test_Clock,
)

ORIGIN = config.LAB_ALLOWED_WEB_ORIGINS[0]


class FakeSocket:
    """Scripted SocketPort: ``inbox`` feeds receive; sends and the close are recorded."""

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

    def texts(self) -> list[dict[str, object]]:
        return [json.loads(f.text) for f in self.sent if f.text]


class Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(self.runs)
        self.store = In_Memory_SessionStore(self.clock)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.store.create("ref-u1", "u1", expires_at=self.clock.now() + timedelta(days=1))
        self.store.create("ref-u2", "u2", expires_at=self.clock.now() + timedelta(days=1))
        self.auth = AuthorizeTerminal(sessions=self.sessions, session_store=self.store)

    def add(self, status: SessionStatus = SessionStatus.READY, *, sandbox: bool = True) -> None:
        now = self.clock.now()
        ref = None
        if sandbox:
            from edu_it_domain.labs.sandbox_spec import build_sandbox_spec

            spec = build_sandbox_spec(_lab(), "s1")
            ref = self.sandbox.provision(spec).sandbox_id
        self.sessions.add_if_no_active(
            LabSession(
                stable_id="s1",
                user_id="u1",
                lab_id="lab1",
                status=status,
                created_at=now,
                ttl_at=now + config.LAB_SESSION_TTL,
                sandbox_ref=ref,
            )
        )

    def gateway(self, *, auto_advance: bool = False) -> TerminalGatewayCore:
        async def sleep(_: float) -> None:
            if auto_advance:
                self.clock.advance(timedelta(seconds=1))
            await asyncio.sleep(0.005)

        return TerminalGatewayCore(
            sessions=self.sessions,
            sandbox=self.sandbox,
            clock=self.clock,
            provider_name="fake",
            sleep=sleep,
        )


def _lab() -> LabDefinition:

    return LabDefinition(
        lab_id="lab1", kind="terminal", image="img", concept_ids=(), tasks=(), checks=()
    )


def _authorize(env: Env, **kw: object) -> object:
    args: dict[str, object] = {"session_ref": "ref-u1", "origin": ORIGIN, "session_id": "s1"}
    args.update(kw)
    return env.auth.execute(**args)  # type: ignore[arg-type]


def test_connect_checks_run_in_order_and_steps_3_4_are_uniform() -> None:
    env = Env()
    env.add()
    ok = _authorize(env)
    assert ok == TerminalAuthorization("s1", "u1")
    # (1) authentication beats a bad origin and a missing session
    r1 = _authorize(env, session_ref=None, origin="https://evil.test", session_id="nope")
    r1b = _authorize(env, session_ref="unknown")
    assert isinstance(r1, TerminalRefusal) and r1 == r1b
    # (2) origin beats a missing session
    r2 = _authorize(env, origin="https://evil.test", session_id="nope")
    assert isinstance(r2, TerminalRefusal) and r2 != r1
    assert _authorize(env, origin=None) == r2
    # (3)/(4) unknown, foreign share one refusal
    unknown = _authorize(env, session_id="nope")
    foreign = _authorize(env, session_ref="ref-u2")
    assert isinstance(unknown, TerminalRefusal)
    assert unknown == foreign
    assert unknown not in (r1, r2)
    # ended is also identical
    env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    assert _authorize(env) == unknown


def test_bytes_flow_resize_and_activity_is_throttled() -> None:
    async def scenario() -> None:
        env = Env()
        env.add()
        sock = FakeSocket()
        gw = env.gateway()
        task = asyncio.create_task(gw.run(TerminalAuthorization("s1", "u1"), sock))
        await sock.inbox.put(Frame(text='{"type":"resize","rows":30,"cols":100}'))
        await sock.inbox.put(Frame(data=b"ls\n"))
        await sock.inbox.put(Frame(data=b"pwd\n"))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if sum(len(f.data or b"") for f in sock.sent) >= 7:
                break
        assert b"".join(f.data or b"" for f in sock.sent) == b"ls\npwd\n"
        session = env.sessions.get("s1")
        assert session is not None and session.last_activity_at == env.clock.now()
        first = session.last_activity_at
        env.clock.advance(timedelta(seconds=1))
        await sock.inbox.put(Frame(data=b"x"))
        await asyncio.sleep(0.05)
        after = env.sessions.get("s1")
        assert after is not None and after.last_activity_at == first  # throttled
        await sock.inbox.put(None)
        assert await task == 1000

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        (Frame(data=b"x" * (config.TERMINAL_MAX_MESSAGE_BYTES + 1)), 1009),
        (Frame(text="not json"), 1003),
    ],
)
def test_limit_violations_close(frame: Frame, code: int) -> None:
    async def scenario() -> int:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await sock.inbox.put(frame)
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(scenario()) == code


def test_rate_limit_closes_1008() -> None:
    async def scenario() -> int:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        for _ in range(config.TERMINAL_MAX_MESSAGES_PER_SECOND + 5):
            await sock.inbox.put(Frame(text='{"type":"pong"}'))
        return await asyncio.wait_for(task, 5)

    assert asyncio.run(scenario()) == 1008


def test_two_missed_pongs_close_4408() -> None:
    async def scenario() -> tuple[int, int]:
        env = Env()
        env.add()
        sock = FakeSocket()
        code = await asyncio.wait_for(
            env.gateway(auto_advance=True).run(TerminalAuthorization("s1", "u1"), sock), 5
        )
        pings = sum(1 for t in sock.texts() if t["type"] == "ping")
        return code, pings

    code, pings = asyncio.run(scenario())
    assert code == 4408
    assert pings == 2


def test_status_frame_precedes_close_when_session_ends() -> None:
    async def scenario() -> FakeSocket:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await asyncio.sleep(0.05)
        env.sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.IDLE_EXPIRED)
        env.clock.advance(config.TERMINAL_STATUS_POLL_INTERVAL)
        await asyncio.wait_for(task, 5)
        return sock

    sock = asyncio.run(scenario())
    status = [t for t in sock.texts() if t["type"] == "status"]
    assert status and status[0]["status"] == "idle-expired"
    assert sock.closed is not None and sock.closed[0] == 1000


def test_new_connection_replaces_old_and_drain_sends_reconnect() -> None:
    async def scenario() -> tuple[FakeSocket, FakeSocket]:
        env = Env()
        env.add()
        gw = env.gateway()
        a, b = FakeSocket(), FakeSocket()
        ta = asyncio.create_task(gw.run(TerminalAuthorization("s1", "u1"), a))
        await asyncio.sleep(0.05)
        tb = asyncio.create_task(gw.run(TerminalAuthorization("s1", "u1"), b))
        assert await asyncio.wait_for(ta, 2) == 4409
        await asyncio.sleep(0.05)
        await gw.registry.drain()
        assert await asyncio.wait_for(tb, 2) == 1012
        return a, b

    a, b = asyncio.run(scenario())
    assert a.closed == (4409, "replaced")
    assert {"type": "reconnect"} in b.texts() and b.closed == (1012, "server-restarting")


def test_run_refuses_session_that_is_no_longer_active_uniformly() -> None:
    async def scenario() -> tuple[int, int]:
        env = Env()
        env.add(SessionStatus.ENDED)
        s1 = FakeSocket()
        c1 = await env.gateway().run(TerminalAuthorization("s1", "u1"), s1)
        s2 = FakeSocket()
        c2 = await env.gateway().run(TerminalAuthorization("missing", "u1"), s2)
        assert s1.closed == s2.closed
        return c1, c2

    assert asyncio.run(scenario()) == (4404, 4404)


def test_terminal_content_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    marker = b"SECRET-MARKER-12345"

    async def scenario() -> None:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await sock.inbox.put(Frame(data=marker))
        await asyncio.sleep(0.1)
        await sock.inbox.put(None)
        await task

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
    assert "SECRET-MARKER" not in caplog.text
    assert "bytes_in=19" in caplog.text
