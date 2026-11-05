"""``AuthorizeTerminal`` and ``TerminalGatewayCore`` (hands-on-labs Req 12, 13.2, 13.5, 27.1).

The core is transport-free: it talks to a :class:`SocketPort` (``receive``,
``send``, ``close``) and a synchronous
:class:`~edu_it_domain.ports.sandbox.TerminalChannel`, so heartbeats, limits, the
one-connection registry, resize, expiry messages and draining are testable
without an ASGI server.

Connect checks run in a fixed order (Req 12.1, 12.2, 12.10, 12.11, 13.2):

1. authentication through the cookie ``SessionStore``;
2. ``Origin`` against ``LAB_ALLOWED_WEB_ORIGINS``;
3. Active_Session state (``provisioning``, ``ready``, ``grading``);
4. ownership, the only authorization decision on connect.

Steps 3 and 4 share one refusal (same close code and reason) so unknown, ended
and foreign sessions are indistinguishable. Message limits and heartbeats apply
only after the connection is accepted. Reconnects are new connections and run
the same four checks.

Nothing carried by terminal frames is logged or stored (Req 12.7): ``INFO``
records session id, byte counts and close codes only. Time is read only through
the injected ``Clock``; every number comes from the domain config.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from edu_it_domain import config as domain_config
from edu_it_domain.labs.limits import HeartbeatTracker, MessageLimiter, MessageVerdict
from edu_it_domain.labs.models import SessionStatus
from edu_it_domain.ports.sandbox import SandboxError

from edu_it_application.labs.save_files import sandbox_handle

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from edu_it_domain.labs.models import LabSession, StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_sessions import LabSessionRepository
    from edu_it_domain.ports.sandbox import SandboxProvider, TerminalChannel
    from edu_it_domain.ports.session_store import SessionStore

__all__ = [
    "CLOSE_DRAIN",
    "CLOSE_HEARTBEAT",
    "CLOSE_NORMAL",
    "CLOSE_ORIGIN",
    "CLOSE_REPLACED",
    "CLOSE_SESSION_REFUSED",
    "CLOSE_UNAUTHENTICATED",
    "INTERRUPTED_MESSAGE_KEY",
    "AuthorizeTerminal",
    "Frame",
    "SocketPort",
    "TerminalAuthorization",
    "TerminalGatewayCore",
    "TerminalRefusal",
    "TerminalRegistry",
    "interrupted_frame",
    "status_frame",
]

_LOG = logging.getLogger("edu_it.labs.terminal")

CLOSE_NORMAL = 1000
CLOSE_UNSUPPORTED = 1003
CLOSE_INTERNAL = 1011
CLOSE_DRAIN = 1012
CLOSE_NOT_READY = 1013
CLOSE_UNAUTHENTICATED = 4401
CLOSE_ORIGIN = 4403
#: The one refusal for steps 3 and 4: unknown, non-active and foreign sessions.
CLOSE_SESSION_REFUSED = 4404
CLOSE_HEARTBEAT = 4408
CLOSE_REPLACED = 4409

#: Translated message key sent when the sandbox was lost to an infrastructure event
#: (kubernetes-deployment Req 19.1, 20.1). Carries no infrastructure detail.
INTERRUPTED_MESSAGE_KEY = "labs.session_interrupted"
_REASON_INTERRUPTED = "session-interrupted"

_REASON_UNAUTHENTICATED = "unauthenticated"
_REASON_ORIGIN = "origin-not-allowed"
_REASON_SESSION = "session-unavailable"

_READ_CHUNK = 64 * 1024
_READ_TIMEOUT_S = 0.25
_MAX_DIMENSION = 1000


@dataclass(frozen=True, slots=True)
class Frame:
    """One WebSocket message: exactly one of ``data`` (binary) or ``text``."""

    data: bytes | None = None
    text: str | None = None

    @property
    def size(self) -> int:
        """Payload size in bytes."""
        if self.data is not None:
            return len(self.data)
        return len((self.text or "").encode("utf-8"))


@runtime_checkable
class SocketPort(Protocol):
    """The transport the core talks to; an ASGI adapter implements it."""

    async def receive(self) -> Frame | None:
        """Next client frame; ``None`` once the peer has disconnected."""
        ...

    async def send(self, frame: Frame) -> None:
        """Send one frame to the client."""
        ...

    async def close(self, code: int, reason: str = "") -> None:
        """Close the socket with ``code``; idempotent."""
        ...


@dataclass(frozen=True, slots=True)
class TerminalAuthorization:
    """An authorized connect: the authenticated owner of an Active_Session."""

    session_id: StableId
    user_id: StableId


@dataclass(frozen=True, slots=True)
class TerminalRefusal:
    """A refused connect. Steps 3 and 4 produce identical values (Req 13.2)."""

    code: int
    reason: str


class AuthorizeTerminal:
    """Run the four connect checks in order and refuse at the first failure."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        session_store: SessionStore,
        config: object = domain_config,
        clock: Clock | None = None,
    ) -> None:
        """Wire the cookie session store and the session repository.

        With a ``clock`` the session ``ttl_at`` is re-checked on every connect (Req 14.8).
        """
        self._sessions = sessions
        self._store = session_store
        self._clock = clock
        self._allowed = tuple(config.LAB_ALLOWED_WEB_ORIGINS)  # type: ignore[attr-defined]

    def _expired(self, session: LabSession) -> bool:
        return self._clock is not None and session.ttl_at <= self._clock.now()

    def execute(
        self, *, session_ref: str | None, origin: str | None, session_id: StableId
    ) -> TerminalAuthorization | TerminalRefusal:
        """Authorize a connect to ``session_id`` or return the refusal."""
        # 1. Authentication (cookie session through the SessionStore).
        resolved = self._store.resolve(session_ref) if session_ref else None
        if resolved is None:
            return TerminalRefusal(CLOSE_UNAUTHENTICATED, _REASON_UNAUTHENTICATED)
        # 2. Origin allowlist; independent of the target session.
        if origin is None or origin not in self._allowed:
            return TerminalRefusal(CLOSE_ORIGIN, _REASON_ORIGIN)
        # 3. Active_Session state.
        session = self._sessions.get(session_id)
        refusal = TerminalRefusal(CLOSE_SESSION_REFUSED, _REASON_SESSION)
        if session is None or not session.status.is_active or self._expired(session):
            return refusal
        # 4. Ownership: the only authorization decision on connect.
        if session.user_id != resolved.user_stable_id:
            return refusal
        return TerminalAuthorization(session_id=session_id, user_id=resolved.user_stable_id)


def status_frame(status: str) -> Frame:
    """The translated ``status`` frame sent before closing (Req 12.6, 12.11)."""
    payload = {
        "type": "status",
        "status": status,
        "message_key": f"labs.terminal.status.{status}",
    }
    return Frame(text=json.dumps(payload, separators=(",", ":")))


def interrupted_frame() -> Frame:
    """The translated ``labs.session_interrupted`` frame; no infrastructure detail."""
    payload = {"type": "status", "status": "interrupted", "message_key": INTERRUPTED_MESSAGE_KEY}
    return Frame(text=json.dumps(payload, separators=(",", ":")))


_RECONNECT_FRAME = Frame(text='{"type":"reconnect"}')
_PING_FRAME = Frame(text='{"type":"ping"}')
_PONG_FRAME = Frame(text='{"type":"pong"}')


class _Connection:
    """One accepted terminal connection; closes at most once."""

    def __init__(
        self, session_id: StableId, socket: SocketPort, heartbeat: HeartbeatTracker
    ) -> None:
        self.session_id = session_id
        self.heartbeat = heartbeat
        self.socket = socket
        self.closed = asyncio.Event()
        self.code: int | None = None
        self.bytes_in = 0
        self.bytes_out = 0

    async def send(self, frame: Frame) -> None:
        if self.closed.is_set():
            return
        try:
            await self.socket.send(frame)
        except Exception:
            await self.close(CLOSE_NORMAL, "send-failed")

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed.is_set():
            return
        self.code = code
        self.closed.set()
        with contextlib.suppress(Exception):
            await self.socket.close(code, reason)


class TerminalRegistry:
    """At most one live connection per session in this process (Req 12.10)."""

    def __init__(self) -> None:
        self._connections: dict[StableId, _Connection] = {}
        self._accepting = True

    def __len__(self) -> int:
        return len(self._connections)

    @property
    def accepting(self) -> bool:
        """``False`` once the drain has stopped new terminal connections (Req 14.2)."""
        return self._accepting

    def stop_accepting(self) -> None:
        """Refuse new terminal connections from now on; idempotent (Req 14.2)."""
        self._accepting = False

    def open_count(self) -> int:
        """Number of registered connections that are not yet closed."""
        return sum(1 for conn in self._connections.values() if not conn.closed.is_set())

    async def add(self, conn: _Connection) -> None:
        if not self._accepting:
            # A connection that slipped past the route check is told to reconnect.
            await conn.send(_RECONNECT_FRAME)
            await conn.close(CLOSE_DRAIN, "server-restarting")
            return
        previous = self._connections.get(conn.session_id)
        self._connections[conn.session_id] = conn
        if previous is not None and previous is not conn:
            await previous.close(CLOSE_REPLACED, "replaced")

    def remove(self, conn: _Connection) -> None:
        if self._connections.get(conn.session_id) is conn:
            del self._connections[conn.session_id]

    async def drain(self) -> None:
        """Tell every open socket to reconnect, then close it with 1012 (Req 12.8).

        Sockets are handled concurrently so one slow peer cannot delay the others.
        """

        async def one(conn: _Connection) -> None:
            await conn.send(_RECONNECT_FRAME)
            await conn.close(CLOSE_DRAIN, "server-restarting")

        await asyncio.gather(*(one(c) for c in list(self._connections.values())))

    async def force_close_all(self) -> None:
        """Close every connection that is still open, without a reconnect message (Req 14.3).

        Only the socket is closed. The lab session and its sandbox are not touched.
        """
        await asyncio.gather(
            *(
                conn.close(CLOSE_DRAIN, "drain-period-elapsed")
                for conn in list(self._connections.values())
                if not conn.closed.is_set()
            )
        )


class TerminalGatewayCore:
    """Pumps bytes between a socket and a sandbox terminal for one connection."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        sandbox: SandboxProvider,
        clock: Clock,
        provider_name: str = "docker",
        registry: TerminalRegistry | None = None,
        config: object = domain_config,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tick_seconds: float = 0.25,
    ) -> None:
        """Wire the ports; ``sleep`` and ``tick_seconds`` pace the supervision loop."""
        self._sessions = sessions
        self._sandbox = sandbox
        self._clock = clock
        self._provider_name = provider_name
        self.registry = registry if registry is not None else TerminalRegistry()
        self._config = config
        self._sleep = sleep
        self._tick = tick_seconds

    async def run(self, authorization: TerminalAuthorization, socket: SocketPort) -> int:
        """Serve one authorized, already accepted connection; return the close code."""
        sid = authorization.session_id
        session = await asyncio.to_thread(self._sessions.get, sid)
        refusal = TerminalRefusal(CLOSE_SESSION_REFUSED, _REASON_SESSION)
        if (
            session is None
            or not session.status.is_active
            or session.ttl_at <= self._clock.now()
            or (session.user_id != authorization.user_id)
        ):
            await socket.close(refusal.code, refusal.reason)
            return refusal.code
        if session.sandbox_ref is None:
            await socket.close(CLOSE_NOT_READY, "not-ready")
            return CLOSE_NOT_READY

        handle = sandbox_handle(sid, session.sandbox_ref, self._provider_name)
        try:
            channel = await asyncio.to_thread(self._sandbox.attach_terminal, handle)
        except SandboxError:
            await socket.close(CLOSE_INTERNAL, "sandbox-unavailable")
            return CLOSE_INTERNAL

        heartbeat = HeartbeatTracker(
            self._clock,
            interval=self._config.TERMINAL_PING_INTERVAL,  # type: ignore[attr-defined]
            max_missed=self._config.TERMINAL_MAX_MISSED_PONGS,  # type: ignore[attr-defined]
        )
        conn = _Connection(sid, socket, heartbeat)
        epoch = await asyncio.to_thread(self._sessions.bump_terminal_epoch, sid)
        await self.registry.add(conn)
        _LOG.info("terminal connected session=%s", sid)
        tasks = [
            asyncio.create_task(self._reader(conn, channel)),
            asyncio.create_task(self._pump(conn, channel)),
            asyncio.create_task(self._supervisor(conn, epoch)),
        ]
        try:
            await conn.closed.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.registry.remove(conn)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(channel.close)
            _LOG.info(
                "terminal closed session=%s code=%s bytes_in=%d bytes_out=%d",
                sid,
                conn.code,
                conn.bytes_in,
                conn.bytes_out,
            )
        return conn.code if conn.code is not None else CLOSE_NORMAL

    # -- input ------------------------------------------------------------

    async def _reader(self, conn: _Connection, channel: TerminalChannel) -> None:
        limiter = MessageLimiter(
            self._clock,
            max_bytes=self._config.TERMINAL_MAX_MESSAGE_BYTES,  # type: ignore[attr-defined]
            messages_per_second=self._config.TERMINAL_MAX_MESSAGES_PER_SECOND,  # type: ignore[attr-defined]
        )
        heartbeat = conn.heartbeat
        last_touch: datetime | None = None
        while not conn.closed.is_set():
            frame = await conn.socket.receive()
            if frame is None:
                await conn.close(CLOSE_NORMAL, "peer-closed")
                return
            verdict = limiter.check(frame.size)
            if verdict is not MessageVerdict.OK:
                reason = "message-too-big" if verdict is MessageVerdict.TOO_BIG else "rate-limited"
                await conn.close(verdict.value, reason)
                return
            if frame.data is not None:
                conn.bytes_in += len(frame.data)
                try:
                    await asyncio.to_thread(channel.write, frame.data)
                except SandboxError:
                    await self._sandbox_closed(conn)
                    return
                last_touch = await self._touch(conn, last_touch)
            elif not await self._control(conn, channel, heartbeat, frame.text or ""):
                return

    async def _touch(self, conn: _Connection, last: datetime | None) -> datetime | None:
        """Record learner activity, at most one write per interval (Req 12.6)."""
        now = self._clock.now()
        interval = self._config.LAB_ACTIVITY_WRITE_INTERVAL  # type: ignore[attr-defined]
        if last is not None and now - last < interval:
            return last
        await asyncio.to_thread(self._sessions.touch_activity, conn.session_id, now)
        return now

    async def _control(
        self,
        conn: _Connection,
        channel: TerminalChannel,
        heartbeat: HeartbeatTracker,
        text: str,
    ) -> bool:
        """Handle a JSON control frame; ``False`` when the connection was closed."""
        try:
            message = json.loads(text)
            kind = message["type"]
        except (ValueError, KeyError, TypeError):
            await conn.close(CLOSE_UNSUPPORTED, "bad-frame")
            return False
        if kind == "pong":
            heartbeat.record_pong()
        elif kind == "ping":
            await conn.send(_PONG_FRAME)
        elif kind == "resize":
            rows, cols = message.get("rows"), message.get("cols")
            if not _valid_dimension(rows) or not _valid_dimension(cols):
                await conn.close(CLOSE_UNSUPPORTED, "bad-frame")
                return False
            try:
                await asyncio.to_thread(channel.resize, rows, cols)
            except SandboxError:
                await self._sandbox_closed(conn)
                return False
        elif kind != "reconnect":
            await conn.close(CLOSE_UNSUPPORTED, "bad-frame")
            return False
        return True

    # -- output -----------------------------------------------------------

    async def _pump(self, conn: _Connection, channel: TerminalChannel) -> None:
        while not conn.closed.is_set():
            try:
                data = await asyncio.to_thread(channel.read, _READ_CHUNK, _READ_TIMEOUT_S)
            except SandboxError:
                await self._sandbox_closed(conn)
                return
            if data:
                conn.bytes_out += len(data)
                await conn.send(Frame(data=data))

    async def _sandbox_closed(self, conn: _Connection) -> None:
        """The sandbox channel ended: close on the session's terms, not the channel's.

        A lost Lab_Pod breaks the channel the instant the node goes, which is *before* the Worker
        reaper detects the loss and marks the session ended-by-infrastructure (cluster node-loss
        detection takes far longer than the channel I/O failure). The Req 19.1/20.1 close bound is
        measured from the platform *detecting* the loss, so the terminal must not close abruptly
        on the bare channel break: it waits for the session status to change and lets
        ``_check_status`` send the translated interrupted + reconnect frames and close CLOSE_DRAIN
        when the mark lands. If the session is already over (``_check_status`` returns True) this
        returns at once. The heartbeat watcher in ``_supervisor`` still bounds a truly hung peer.
        """
        if await self._check_status(conn, None):
            return
        while not conn.closed.is_set():
            await self._sleep(self._tick)
            if conn.closed.is_set():
                return
            if await self._check_status(conn, None):
                return

    # -- supervision ------------------------------------------------------

    async def _supervisor(self, conn: _Connection, epoch: int | None) -> None:
        """Heartbeat and 2 s status watcher, paced by the injected sleep."""
        heartbeat = conn.heartbeat
        poll = self._config.TERMINAL_STATUS_POLL_INTERVAL  # type: ignore[attr-defined]
        last_poll = self._clock.now()
        while not conn.closed.is_set():
            await self._sleep(self._tick)
            if conn.closed.is_set():
                return
            if heartbeat.ping_due():
                heartbeat.record_ping()
                if heartbeat.should_close:
                    await conn.close(CLOSE_HEARTBEAT, "heartbeat-timeout")
                    return
                await conn.send(_PING_FRAME)
            now = self._clock.now()
            if now - last_poll >= poll:
                last_poll = now
                if await self._check_status(conn, epoch):
                    return

    async def _check_status(self, conn: _Connection, epoch: int | None) -> bool:
        """Re-read the session; close if it is gone, over, or superseded."""
        session: LabSession | None = await asyncio.to_thread(self._sessions.get, conn.session_id)
        if epoch is not None and session is not None and session.terminal_epoch != epoch:
            await conn.close(CLOSE_REPLACED, "replaced")
            return True
        if session is not None and session.status.is_active:
            return False
        if session is not None and session.status is SessionStatus.ENDED_BY_INFRASTRUCTURE:
            # Node loss (Req 19.1, 20.1): translated message and reconnect indication only.
            await conn.send(interrupted_frame())
            await conn.send(_RECONNECT_FRAME)
            await conn.close(CLOSE_DRAIN, _REASON_INTERRUPTED)
            return True
        status = "ended" if session is None else session.status.value
        await conn.send(status_frame(status))
        await conn.close(CLOSE_NORMAL, "session-over")
        return True


def _valid_dimension(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= _MAX_DIMENSION
