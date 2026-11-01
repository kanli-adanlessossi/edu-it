"""Terminal WebSocket route ``WS /v1/lab-sessions/{id}/terminal`` (task 10.6).

A thin ASGI boundary over ``AuthorizeTerminal`` and ``TerminalGatewayCore``
(``edu_it_application.labs.terminal``). The connect checks run before the
handshake is accepted, in the fixed order of Req 12.1, 12.2, 12.10, 12.11:
cookie session through the ``SessionStore`` (ADR 0004), ``Origin`` allowlist,
Active_Session state, ownership. A refused connect is closed without accepting
(the client sees the handshake rejection) using the refusal's code and reason;
the last two checks share one refusal. Terminal bytes travel as binary frames;
``resize``, ``ping``/``pong``, ``reconnect`` and ``status`` are text JSON frames.

Frame contents are never logged here (Req 12.7).
"""

from __future__ import annotations

import contextlib

from edu_it_application.labs import (
    AuthorizeTerminal,
    Frame,
    TerminalGatewayCore,
    TerminalRefusal,
)
from fastapi import APIRouter, WebSocket
from starlette.concurrency import run_in_threadpool

__all__ = ["SESSION_COOKIE_NAME", "StarletteSocket", "add_terminal_route"]

#: Cookie holding the opaque session reference (same name the web app uses).
SESSION_COOKIE_NAME = "edu_it_session"


class StarletteSocket:
    """:class:`~edu_it_application.labs.SocketPort` over an accepted Starlette socket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._closed = False

    async def receive(self) -> Frame | None:
        """Next client frame; ``None`` once the peer has disconnected."""
        if self._closed:
            return None
        try:
            message = await self._ws.receive()
        except RuntimeError:
            self._closed = True
            return None
        if message["type"] != "websocket.receive":
            self._closed = True
            return None
        data = message.get("bytes")
        if data is not None:
            return Frame(data=data)
        return Frame(text=message.get("text") or "")

    async def send(self, frame: Frame) -> None:
        """Send one frame; binary for terminal bytes, text for control JSON."""
        if frame.data is not None:
            await self._ws.send_bytes(frame.data)
        else:
            await self._ws.send_text(frame.text or "")

    async def close(self, code: int, reason: str = "") -> None:
        """Close with ``code``; idempotent and tolerant of a gone peer."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._ws.close(code=code, reason=reason)


def add_terminal_route(
    router: APIRouter,
    *,
    authorize: AuthorizeTerminal,
    gateway: TerminalGatewayCore,
    cookie_name: str = SESSION_COOKIE_NAME,
) -> None:
    """Register ``WS /lab-sessions/{session_id}/terminal`` on the ``/v1`` router."""

    @router.websocket("/lab-sessions/{session_id}/terminal")
    async def terminal(websocket: WebSocket, session_id: str) -> None:
        outcome = await run_in_threadpool(
            authorize.execute,
            session_ref=websocket.cookies.get(cookie_name),
            origin=websocket.headers.get("origin"),
            session_id=session_id,
        )
        if isinstance(outcome, TerminalRefusal):
            await websocket.close(code=outcome.code, reason=outcome.reason)
            return
        await websocket.accept()
        await gateway.run(outcome, StarletteSocket(websocket))
