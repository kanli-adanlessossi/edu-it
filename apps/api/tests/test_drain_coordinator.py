"""Unit tests for ``DrainCoordinator`` and the admin drain endpoint (task 9.1).

Requirement 14.1, 14.2, 14.3, 14.9 and 14.12. Time is virtual: the coordinator gets an
injected ``Clock`` and an injected ``sleep``, so no test waits on the wall clock.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import signal
import threading
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from edu_it_api.drain import DRAIN_PATH, DrainCoordinator, build_admin_app, validate_drain_settings
from edu_it_api.health import build_health_router
from edu_it_api.serve import DrainingServer
from edu_it_api.terminal import DRAIN_REFUSAL_REASON, add_terminal_route
from edu_it_application.labs import Frame, TerminalRegistry
from edu_it_application.labs.terminal import CLOSE_DRAIN, _Connection
from edu_it_domain import config
from edu_it_domain.ports.health import DependencyProbe
from fastapi import APIRouter, FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st

_RECONNECT = '{"type":"reconnect"}'


class VirtualTime:
    """Virtual seconds: ``sleep`` parks until ``advance`` reaches its wake-up time."""

    def __init__(self) -> None:
        self.seconds = 0.0
        self._waiters: list[tuple[float, int, asyncio.Future[None]]] = []
        self._count = 0
        self.sleeps: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._count += 1
        heapq.heappush(self._waiters, (self.seconds + delay, self._count, future))
        await future

    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        target = self.seconds + seconds
        await self.settle()
        while self._waiters and self._waiters[0][0] <= target:
            wake, _, future = heapq.heappop(self._waiters)
            self.seconds = wake
            if not future.done():
                future.set_result(None)
            await self.settle()
        self.seconds = target
        await self.settle()

    def now(self) -> datetime:
        return datetime(2030, 1, 1, tzinfo=UTC) + timedelta(seconds=self.seconds)


class FakeSocket:
    """A ``SocketPort`` that records what the drain does to it."""

    def __init__(self, *, hang_on_send: bool = False) -> None:
        self.sent: list[str] = []
        self.closes: list[tuple[int, str]] = []
        self._hang = hang_on_send

    async def receive(self) -> Frame | None:
        await asyncio.sleep(3600)
        return None

    async def send(self, frame: Frame) -> None:
        if self._hang:
            await asyncio.Event().wait()
        self.sent.append(frame.text or "")

    async def close(self, code: int, reason: str = "") -> None:
        self.closes.append((code, reason))


def _connection(session_id: str, socket: FakeSocket) -> _Connection:
    return _Connection(session_id, socket, heartbeat=None)  # type: ignore[arg-type]


async def _setup(
    *sockets: FakeSocket,
    drain_period: int = config.API_DRAIN_PERIOD_DEFAULT_SECONDS,
    prestop: int = config.API_PRESTOP_DELAY_DEFAULT_SECONDS,
) -> tuple[DrainCoordinator, TerminalRegistry, VirtualTime]:
    vt = VirtualTime()
    registry = TerminalRegistry()
    for index, sock in enumerate(sockets):
        await registry.add(_connection(f"s{index}", sock))
    coordinator = DrainCoordinator(
        registry,
        vt,
        drain_period_seconds=drain_period,
        prestop_delay_seconds=prestop,
        sleep=vt.sleep,
    )
    return coordinator, registry, vt


def _run(scenario: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(scenario())


async def _asgi_get(app: Any, path: str) -> tuple[int, dict[str, Any]]:
    """Issue one ``GET`` straight into the ASGI app; return status and JSON body."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body) if body else {}


async def _asgi_ws_first_message(app: Any, path: str) -> dict[str, Any]:
    """Open a WebSocket and return the first message the server sends back."""
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "subprotocols": [],
    }
    to_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    from_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    to_server.put_nowait({"type": "websocket.connect"})
    task = asyncio.create_task(app(scope, to_server.get, from_server.put))
    message = await asyncio.wait_for(from_server.get(), 5)
    await asyncio.wait_for(task, 5)
    return message


def test_readiness_is_503_before_the_prestop_delay_and_sockets_stay_open() -> None:
    async def scenario() -> None:
        sock = FakeSocket()
        coordinator, registry, vt = await _setup(sock)
        assert coordinator.draining is False

        task = asyncio.ensure_future(coordinator.start_drain())
        await vt.settle()
        # Readiness already reports not-ready, the drain has not started (Req 14.1, 14.12).
        assert coordinator.draining is True
        assert registry.accepting is True
        assert sock.sent == [] and sock.closes == []

        router = build_health_router(lambda: [], is_draining=lambda: coordinator.draining)
        ready = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/ready")
        response = ready()
        assert response.status_code == 503

        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        assert registry.accepting is False
        await task

    _run(scenario)


def test_drain_sends_reconnect_then_closes_every_socket_within_the_period() -> None:
    async def scenario() -> None:
        sockets = [FakeSocket(), FakeSocket(), FakeSocket()]
        coordinator, registry, vt = await _setup(*sockets)
        task = asyncio.ensure_future(coordinator.start_drain())
        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        report = await task

        for sock in sockets:
            assert sock.sent == [_RECONNECT]
            assert sock.closes == [(CLOSE_DRAIN, "server-restarting")]
        assert report.forced is False
        assert registry.open_count() == 0
        assert (report.drain_started_at - report.started_at).total_seconds() == (
            config.API_PRESTOP_DELAY_DEFAULT_SECONDS
        )
        assert coordinator.report == report

    _run(scenario)


def test_socket_still_open_at_the_drain_period_is_force_closed() -> None:
    async def scenario() -> None:
        stuck, healthy = FakeSocket(hang_on_send=True), FakeSocket()
        coordinator, registry, vt = await _setup(stuck, healthy, drain_period=5, prestop=2)
        task = asyncio.ensure_future(coordinator.start_drain())

        await vt.advance(2)  # preStop delay over, drain running
        assert registry.accepting is False
        await vt.advance(4)  # one second short of the drain period
        assert task.done() is False
        assert stuck.closes == []

        await vt.advance(1)  # drain period reached
        report = await task
        assert report.forced is True
        assert stuck.closes == [(CLOSE_DRAIN, "drain-period-elapsed")]
        assert healthy.closes == [(CLOSE_DRAIN, "server-restarting")]
        assert registry.open_count() == 0
        assert (report.finished_at - report.drain_started_at).total_seconds() == 5

    _run(scenario)


def test_start_drain_is_idempotent_and_runs_the_sequence_once() -> None:
    async def scenario() -> None:
        sock = FakeSocket()
        coordinator, _registry, vt = await _setup(sock)
        first = asyncio.ensure_future(coordinator.start_drain())
        second = asyncio.ensure_future(coordinator.start_drain())  # e.g. SIGTERM after preStop
        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        reports = await asyncio.gather(first, second)
        late = await coordinator.start_drain()

        assert reports[0] is reports[1] is late
        assert sock.sent == [_RECONNECT]
        assert len(sock.closes) == 1
        assert vt.sleeps == [
            config.API_PRESTOP_DELAY_DEFAULT_SECONDS,
            config.API_DRAIN_PERIOD_DEFAULT_SECONDS,
        ]

    _run(scenario)


def test_a_cancelled_caller_does_not_abort_the_drain() -> None:
    async def scenario() -> None:
        sock = FakeSocket()
        coordinator, _registry, vt = await _setup(sock)
        caller = asyncio.ensure_future(coordinator.start_drain())
        await vt.settle()
        caller.cancel()  # the preStop request went away
        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        assert (await coordinator.start_drain()).forced is False
        assert sock.closes == [(CLOSE_DRAIN, "server-restarting")]

    _run(scenario)


def test_a_connection_added_after_the_drain_started_is_told_to_reconnect() -> None:
    async def scenario() -> None:
        coordinator, registry, vt = await _setup()
        task = asyncio.ensure_future(coordinator.start_drain())
        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        await task

        late = FakeSocket()
        await registry.add(_connection("late", late))
        assert late.sent == [_RECONNECT]
        assert late.closes == [(CLOSE_DRAIN, "server-restarting")]
        assert len(registry) == 0

    _run(scenario)


def test_drain_writes_no_terminal_data() -> None:
    # The only frame the drain sends is the reconnect control message (Req 14.9).
    async def scenario() -> None:
        sock = FakeSocket()
        coordinator, _registry, vt = await _setup(sock)
        task = asyncio.ensure_future(coordinator.start_drain())
        await vt.advance(config.API_PRESTOP_DELAY_DEFAULT_SECONDS)
        await task
        assert sock.sent == [_RECONNECT]

    _run(scenario)


@pytest.mark.parametrize(("drain", "prestop"), [(4, 5), (301, 5), (30, -1), (30, 61)])
def test_out_of_range_settings_are_rejected(drain: int, prestop: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        validate_drain_settings(drain, prestop)
    with pytest.raises(ValueError, match="outside"):
        DrainCoordinator(
            TerminalRegistry(),
            VirtualTime(),
            drain_period_seconds=drain,
            prestop_delay_seconds=prestop,
        )


@pytest.mark.parametrize("drain", [5, 30, 300])
def test_period_bounds_are_accepted(drain: int) -> None:
    validate_drain_settings(drain, config.API_PRESTOP_DELAY_DEFAULT_SECONDS)


def test_defaults_come_from_the_domain_config() -> None:
    assert config.API_DRAIN_PERIOD_DEFAULT_SECONDS == 30
    assert config.API_DRAIN_PERIOD_BOUNDS_SECONDS == (5, 300)
    assert config.API_PRESTOP_DELAY_DEFAULT_SECONDS == 5


def test_admin_endpoint_runs_the_drain_and_is_idempotent() -> None:
    async def instant(_delay: float) -> None:
        await asyncio.sleep(0)

    async def scenario() -> None:
        registry = TerminalRegistry()
        coordinator = DrainCoordinator(registry, VirtualTime(), sleep=instant)
        app = build_admin_app(coordinator)
        first = await _asgi_get(app, DRAIN_PATH)
        second = await _asgi_get(app, DRAIN_PATH)
        assert first == (200, {"status": "drained", "forced": False})
        assert second == first
        assert coordinator.draining is True
        assert registry.accepting is False

    _run(scenario)


def test_admin_app_exposes_nothing_but_the_drain_endpoint() -> None:
    async def scenario() -> None:
        coordinator = DrainCoordinator(TerminalRegistry(), VirtualTime())
        app = build_admin_app(coordinator)
        for path in ("/health", "/ready", "/docs", "/openapi.json", "/v1/anything"):
            status, _body = await _asgi_get(app, path)
            assert status == 404, path
        assert coordinator.draining is False  # none of those started a drain

    _run(scenario)


def test_terminal_upgrade_is_refused_with_the_drain_close_code_while_draining() -> None:
    registry = TerminalRegistry()

    class _MustNotAuthorize:
        def execute(self, **_kwargs: object) -> None:
            raise AssertionError("authorization must not run while draining")

    router = APIRouter()
    add_terminal_route(
        router,
        authorize=_MustNotAuthorize(),  # type: ignore[arg-type]
        gateway=SimpleNamespace(registry=registry),  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(router)

    registry.stop_accepting()

    async def scenario() -> None:
        message = await _asgi_ws_first_message(app, "/lab-sessions/abc/terminal")
        # Closed before accept, with the reconnect close code (Req 14.2).
        assert message["type"] == "websocket.close"
        assert message["code"] == CLOSE_DRAIN
        assert message["reason"] == DRAIN_REFUSAL_REASON

    _run(scenario)


def test_readiness_is_not_ready_while_draining_even_if_dependencies_are_reachable() -> None:
    reachable = [DependencyProbe(name="postgresql", is_reachable=lambda: True)]
    draining = {"value": False}
    router = build_health_router(lambda: reachable, is_draining=lambda: draining["value"])
    ready = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/ready")
    live = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/health")

    assert ready().status_code == 200
    draining["value"] = True
    assert ready().status_code == 503
    assert live().status_code == 200  # liveness is unaffected


def test_sigterm_handler_calls_the_same_start_drain() -> None:
    calls: list[str] = []

    class _Coordinator:
        draining = False

        async def start_drain(self) -> None:
            calls.append("start_drain")
            await asyncio.sleep(0.05)

    server_config = uvicorn.Config(FastAPI(), host="127.0.0.1", port=0, log_level="warning")
    server = DrainingServer(server_config, TerminalRegistry(), _Coordinator())  # type: ignore[arg-type]

    async def scenario() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        server.handle_exit(signal.SIGTERM, None)
        server.handle_exit(signal.SIGTERM, None)  # a second signal never starts a second drain
        await asyncio.wait_for(task, 10)

    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(scenario())
        except BaseException as exc:
            errors.append(exc)

    # uvicorn installs signal handlers on the main thread only; run off it.
    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    assert not errors, errors
    assert calls == ["start_drain"]


class _SlowCloseSocket(FakeSocket):
    """Accepts the reconnect message but takes longer than any drain period to close."""

    def __init__(self, vt: VirtualTime, close_seconds: float) -> None:
        super().__init__()
        self._vt = vt
        self._close_seconds = close_seconds

    async def close(self, code: int, reason: str = "") -> None:
        self.closes.append((code, reason))
        await self._vt.sleep(self._close_seconds)


_NORMAL, _SLOW_CLOSE, _STUCK_SEND = "normal", "slow-close", "stuck-send"


# Feature: kubernetes-deployment, Property 16: Drain closes or force-closes every socket
# **Validates: Requirements 14.1, 14.2, 14.3**
# The coordinator holds no sandbox port, so Lab_Pods cannot be touched by it (Req 14.3).
@settings(max_examples=100, deadline=None)
@given(
    kinds=st.lists(st.sampled_from([_NORMAL, _SLOW_CLOSE, _STUCK_SEND]), max_size=8),
    drain_period=st.integers(*config.API_DRAIN_PERIOD_BOUNDS_SECONDS),
    prestop=st.integers(*config.API_PRESTOP_DELAY_BOUNDS_SECONDS),
)
def test_property_16_drain_closes_or_force_closes_every_socket(
    kinds: list[str], drain_period: int, prestop: int
) -> None:
    async def scenario() -> None:
        vt = VirtualTime()
        sockets: list[FakeSocket] = [
            _SlowCloseSocket(vt, drain_period + 1)
            if kind == _SLOW_CLOSE
            else FakeSocket(hang_on_send=kind == _STUCK_SEND)
            for kind in kinds
        ]
        registry = TerminalRegistry()
        for index, sock in enumerate(sockets):
            await registry.add(_connection(f"s{index}", sock))
        coordinator = DrainCoordinator(
            registry,
            vt,
            drain_period_seconds=drain_period,
            prestop_delay_seconds=prestop,
            sleep=vt.sleep,
        )
        router = build_health_router(lambda: [], is_draining=lambda: coordinator.draining)
        ready = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/ready")

        task = asyncio.ensure_future(coordinator.start_drain())
        await vt.settle()
        # Readiness is false from the first call, before any socket is touched (Req 14.1).
        assert coordinator.draining is True
        assert ready().status_code == 503
        if prestop > 0:
            assert registry.accepting is True
            assert all(s.sent == [] and s.closes == [] for s in sockets)

        await vt.advance(prestop)
        # New upgrades are rejected once the drain starts (Req 14.2).
        assert registry.accepting is False

        await vt.advance(drain_period)
        report = await task

        for kind, sock in zip(kinds, sockets, strict=True):
            # Every socket is closed with the drain code, exactly once (Req 14.2, 14.3).
            assert len(sock.closes) == 1 and sock.closes[0][0] == CLOSE_DRAIN
            if kind == _STUCK_SEND:
                # Never accepted the message; still open at the deadline, so force-closed.
                assert sock.sent == []
                assert sock.closes[0][1] == "drain-period-elapsed"
            else:
                assert sock.sent == [_RECONNECT]
                assert sock.closes[0][1] == "server-restarting"
        assert registry.open_count() == 0

        slow = any(kind != _NORMAL for kind in kinds)
        assert report.forced is slow
        elapsed = (report.finished_at - report.drain_started_at).total_seconds()
        assert (elapsed == drain_period) if slow else (elapsed == 0)
        assert (report.drain_started_at - report.started_at).total_seconds() == prestop

        late = FakeSocket()
        await registry.add(_connection("late", late))
        assert late.sent == [_RECONNECT]
        assert late.closes == [(CLOSE_DRAIN, "server-restarting")]

    _run(scenario)
