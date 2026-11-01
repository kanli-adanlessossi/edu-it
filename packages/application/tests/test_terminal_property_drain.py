"""Property 31: graceful shutdown drains every terminal (task 10.8).

Feature: hands-on-labs, Property 31: Graceful shutdown drains every terminal

**Validates: Requirements 12.8, 26.5**
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from edu_it_application.labs import Frame, TerminalAuthorization
from edu_it_application.labs.terminal import TerminalRegistry, _Connection
from edu_it_domain import config
from edu_it_domain.labs.limits import HeartbeatTracker
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st


def _load_gateway_helpers() -> object:
    """Load the sibling test module by path (tests are not an importable package)."""
    path = Path(__file__).with_name("test_terminal_gateway.py")
    spec = importlib.util.spec_from_file_location("_terminal_gateway_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_helpers = _load_gateway_helpers()
Env = _helpers.Env  # type: ignore[attr-defined]
FakeSocket = _helpers.FakeSocket  # type: ignore[attr-defined]
_lab = _helpers._lab  # type: ignore[attr-defined]

RECONNECT = {"type": "reconnect"}


class _OrderedSocket(FakeSocket):  # type: ignore[valid-type, misc]
    """Records the order of sends and the close in one event list."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def send(self, frame: Frame) -> None:
        self.events.append(f"send:{frame.text}")
        await super().send(frame)

    async def close(self, code: int, reason: str = "") -> None:
        self.events.append(f"close:{code}")
        await super().close(code, reason)


def _assert_drained(sock: _OrderedSocket) -> None:
    assert sock.closed is not None and sock.closed[0] == 1012
    assert sock.texts().count(RECONNECT) == 1
    closes = [i for i, e in enumerate(sock.events) if e.startswith("close:")]
    reconnect = (
        sock.events.index('send:{"type":"reconnect"}')
        if ('send:{"type":"reconnect"}' in sock.events)
        else next(i for i, e in enumerate(sock.events) if "reconnect" in e)
    )
    assert closes == [closes[0]] and reconnect < closes[0]


@settings(max_examples=100, deadline=None)
@given(
    flags=st.lists(st.booleans(), min_size=0, max_size=25),  # True = already closed
)
def test_drain_reconnects_then_closes_every_open_socket(flags: list[bool]) -> None:
    """Feature: hands-on-labs, Property 31: Graceful shutdown drains every terminal."""

    async def scenario() -> list[tuple[bool, _OrderedSocket, _Connection]]:
        registry = TerminalRegistry()
        clock = Test_Clock()
        out = []
        for i, already_closed in enumerate(flags):
            sock = _OrderedSocket()
            conn = _Connection(
                f"s{i}",
                sock,
                HeartbeatTracker(
                    clock,
                    interval=config.TERMINAL_PING_INTERVAL,
                    max_missed=config.TERMINAL_MAX_MISSED_PONGS,
                ),
            )
            await registry.add(conn)
            if already_closed:
                await conn.close(1000, "client-gone")
                sock.events.clear()
                sock.sent.clear()
            out.append((already_closed, sock, conn))
        await registry.drain()
        await registry.drain()  # idempotent: nothing is sent twice
        return out

    for already_closed, sock, conn in asyncio.run(scenario()):
        if already_closed:
            assert sock.events == [] and conn.code == 1000
        else:
            _assert_drained(sock)
            assert conn.code == 1012


@settings(max_examples=100, deadline=None)
@given(n=st.integers(min_value=1, max_value=6))
def test_gateway_drain_ends_every_running_connection_with_1012(n: int) -> None:
    """Feature: hands-on-labs, Property 31: Graceful shutdown drains every terminal."""

    async def scenario() -> tuple[list[int], list[_OrderedSocket]]:
        env = Env()
        now = env.clock.now()
        gw = env.gateway()
        socks = [_OrderedSocket() for _ in range(n)]
        tasks = []
        for i, sock in enumerate(socks):
            ref = env.sandbox.provision(build_sandbox_spec(_lab(), f"s{i}")).sandbox_id
            env.sessions.add_if_no_active(
                LabSession(
                    stable_id=f"s{i}",
                    user_id=f"u{i}",
                    lab_id="lab1",
                    status=SessionStatus.READY,
                    created_at=now,
                    ttl_at=now + config.LAB_SESSION_TTL,
                    sandbox_ref=ref,
                )
            )
            tasks.append(asyncio.create_task(gw.run(TerminalAuthorization(f"s{i}", f"u{i}"), sock)))
        for _ in range(200):
            if len(gw.registry) == n:
                break
            await asyncio.sleep(0.005)
        assert len(gw.registry) == n
        await gw.registry.drain()
        codes = [await asyncio.wait_for(t, 5) for t in tasks]
        return codes, socks

    codes, socks = asyncio.run(scenario())
    assert codes == [1012] * n
    for sock in socks:
        _assert_drained(sock)
