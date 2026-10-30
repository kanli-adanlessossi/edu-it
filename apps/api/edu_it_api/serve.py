"""API entrypoint with a draining uvicorn server (Req 12.8, 26.5).

On SIGINT/SIGTERM the stock uvicorn server stops accepting connections and waits
for open ones to finish, which never happens for long-lived terminal WebSockets.
:class:`DrainingServer` overrides ``handle_exit`` so that the first signal first
runs ``registry.drain()`` (each open terminal receives ``{"type":"reconnect"}``
and is closed with 1012) and only then falls through to uvicorn's normal
shutdown. A repeated signal skips the drain and shuts down immediately.

Run with ``python -m edu_it_api.serve``.
"""

from __future__ import annotations

import asyncio
import os
import socket
from types import FrameType

import uvicorn
from edu_it_application.labs.terminal import TerminalRegistry
from fastapi import FastAPI

from edu_it_api.app import create_app

__all__ = ["DrainingServer", "main"]


class DrainingServer(uvicorn.Server):
    """``uvicorn.Server`` that drains terminal sockets before shutting down."""

    def __init__(self, config: uvicorn.Config, registry: TerminalRegistry) -> None:
        super().__init__(config)
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._draining = False
        self._drain_task: asyncio.Task[None] | None = None

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        await super().serve(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        loop = self._loop
        if self._draining or loop is None or self.should_exit:
            # Second signal (or no running loop): normal uvicorn behaviour.
            super().handle_exit(sig, frame)
            return
        self._draining = True
        loop.call_soon_threadsafe(self._start_drain, sig, frame)

    def _start_drain(self, sig: int, frame: FrameType | None) -> None:
        self._drain_task = asyncio.ensure_future(self._drain_then_exit(sig, frame))

    async def _drain_then_exit(self, sig: int, frame: FrameType | None) -> None:
        try:
            await self._registry.drain()
        finally:
            # Normal shutdown (sets should_exit, records the signal) only after the drain.
            super().handle_exit(sig, frame)


def build_served_app() -> tuple[FastAPI, TerminalRegistry]:
    """Compose the served app: the real labs router (PostgreSQL, Redis, sandbox provider).

    The content root is read from ``EDU_IT_CONTENT_ROOT`` (default ``content``). The
    returned registry is the one the terminal gateway uses, so draining it reaches every
    open terminal.
    """
    from edu_it_api.composition import build_lab_router

    labs = build_lab_router(content_root=os.environ.get("EDU_IT_CONTENT_ROOT", "content"))
    return create_app(lab_router=labs.router), labs.registry


def main() -> None:
    """Build the app and serve it with the draining server."""
    app, registry = build_served_app()
    config = uvicorn.Config(
        app,
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
    )
    DrainingServer(config, registry).run()


if __name__ == "__main__":  # pragma: no cover
    main()
