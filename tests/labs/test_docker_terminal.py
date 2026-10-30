"""Docker terminal tests (hands-on-labs task 10.10).

Requirements 12.3, 12.4, 12.6, 12.12, 13.1, 13.3 against ``DockerSandboxProvider`` driven through
the real ``TerminalGatewayCore``: a real TTY is attached, resize reaches the PTY, typing records
learner activity, the sandbox outlives every WebSocket, and workspace files survive a reconnect.
The socket is an in-process ``SocketPort``; the container is real. Tests carry the ``docker``
marker and skip when no daemon or no local ``edu-it/lab-linux-base:dev`` image is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest
from edu_it_application.labs import Frame, TerminalAuthorization, TerminalGatewayCore
from edu_it_domain import config as cfg
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabDefinition
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider

IMAGE = "edu-it/lab-linux-base:dev"
SID = "term-docker-1"
_LAB = LabDefinition(
    lab_id="term-lab", kind="terminal", image=IMAGE, concept_ids=(), tasks=(), checks=()
)
_LOCAL = {"as_user": "learner", "env": {}, "cwd": "/workspace"}


def _daemon_ready() -> bool:
    try:
        import docker

        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(IMAGE)
    except Exception:
        return False
    return True


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _daemon_ready(), reason="no Docker daemon or lab image"),
]


class _Socket:
    """In-process SocketPort: ``feed`` plays the browser, ``output`` collects terminal bytes."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue[Frame | None] = asyncio.Queue()
        self.output = b""
        self.closed: tuple[int, str] | None = None

    async def receive(self) -> Frame | None:
        return await self.inbox.get()

    async def send(self, frame: Frame) -> None:
        if frame.data is not None:
            self.output += frame.data

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed is None:
            self.closed = (code, reason)

    def type(self, text: str) -> None:
        self.inbox.put_nowait(Frame(data=text.encode()))

    def resize(self, rows: int, cols: int) -> None:
        msg = json.dumps({"type": "resize", "rows": rows, "cols": cols})
        self.inbox.put_nowait(Frame(text=msg))

    async def wait_for(self, needle: bytes, timeout: float = 15.0) -> bytes:
        deadline = time.monotonic() + timeout
        while needle not in self.output:
            if time.monotonic() > deadline:
                raise AssertionError(f"{needle!r} not seen in {self.output!r}")
            await asyncio.sleep(0.05)
        return self.output


class _Env:
    def __init__(self, provider: DockerSandboxProvider) -> None:
        self.provider = provider
        self.clock = Test_Clock()
        self.sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
        self.handle: SandboxHandle = provider.provision(build_sandbox_spec(_LAB, SID))
        now = self.clock.now()
        self.sessions.add_if_no_active(
            LabSession(
                stable_id=SID,
                user_id="u1",
                lab_id=_LAB.lab_id,
                status=SessionStatus.READY,
                created_at=now,
                ttl_at=now + cfg.LAB_SESSION_TTL,
                sandbox_ref=self.handle.sandbox_id,
            )
        )
        self.gateway = TerminalGatewayCore(
            sessions=self.sessions,
            sandbox=provider,
            clock=self.clock,
            provider_name="docker",
        )

    @contextlib.asynccontextmanager
    async def connect(self):  # type: ignore[no-untyped-def]
        """Run one gateway connection; the browser disconnects when the block ends."""
        sock = _Socket()
        task = asyncio.create_task(self.gateway.run(TerminalAuthorization(SID, "u1"), sock))
        try:
            yield sock
        finally:
            sock.inbox.put_nowait(None)
            await asyncio.wait_for(task, timeout=15)

    def last_activity(self):  # type: ignore[no-untyped-def]
        session = self.sessions.get(SID)
        assert session is not None
        return session.last_activity_at

    def sh(self, command: str) -> bytes:
        res = self.provider.exec(self.handle, ["/bin/bash", "-c", command], **_LOCAL)  # type: ignore[arg-type]
        return res.stdout


@pytest.fixture
def env() -> Iterator[_Env]:
    e = _Env(DockerSandboxProvider())
    try:
        yield e
    finally:
        e.provider.destroy(e.handle)


def test_real_tty_is_attached(env: _Env) -> None:
    async def scenario() -> None:
        async with env.connect() as sock:
            sock.type("tty; echo TTY$((20+22))\n")
            out = await sock.wait_for(b"TTY42")
            assert b"/dev/pts/" in out  # a real pseudo-terminal, not a pipe ("not a tty")
            assert b"not a tty" not in out

    asyncio.run(scenario())


def test_resize_applies_rows_and_columns(env: _Env) -> None:
    async def scenario() -> None:
        async with env.connect() as sock:
            sock.resize(37, 113)
            sock.type("echo SZ$(stty size | tr ' ' x)\n")
            await sock.wait_for(b"SZ37x113")

    asyncio.run(scenario())


def test_typing_records_learner_activity(env: _Env) -> None:
    assert env.last_activity() is None

    async def scenario() -> None:
        async with env.connect() as sock:
            sock.type("echo ACT$((1+1))\n")
            await sock.wait_for(b"ACT2")

    asyncio.run(scenario())
    assert env.last_activity() == env.clock.now()

    # Activity writes are throttled to LAB_ACTIVITY_WRITE_INTERVAL; later typing moves them on.
    first = env.clock.now()
    env.clock.advance(cfg.LAB_ACTIVITY_WRITE_INTERVAL + timedelta(seconds=1))

    async def again() -> None:
        async with env.connect() as sock:
            sock.type("echo ACT$((2+2))\n")
            await sock.wait_for(b"ACT4")

    asyncio.run(again())
    assert env.last_activity() == env.clock.now() > first


def test_sandbox_stays_alive_with_no_websocket_connected(env: _Env) -> None:
    async def scenario() -> None:
        async with env.connect() as sock:
            sock.type("sleep 1000 &\necho BG$((1+1))\n")
            await sock.wait_for(b"BG2")

    asyncio.run(scenario())  # the browser is gone; nobody is connected from here on

    time.sleep(2)
    assert env.handle in env.provider.list_sandboxes()
    assert env.provider.health(env.handle).healthy
    session = env.sessions.get(SID)
    assert session is not None and session.status is SessionStatus.READY
    assert env.sh("echo alive").strip() == b"alive"


def test_files_survive_a_reconnect(env: _Env) -> None:
    async def first() -> None:
        async with env.connect() as sock:
            sock.type("echo kept-$((6*7)) > /workspace/keep.txt; echo W$((1+1))\n")
            await sock.wait_for(b"W2")

    asyncio.run(first())
    assert env.sh("cat /workspace/keep.txt").strip() == b"kept-42"

    async def second() -> None:
        async with env.connect() as sock:
            sock.type("cat /workspace/keep.txt; echo R$((1+1))\n")
            out = await sock.wait_for(b"R2")
            assert b"kept-42" in out

    asyncio.run(second())
