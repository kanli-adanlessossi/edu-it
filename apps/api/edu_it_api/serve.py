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
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import uvicorn
from edu_it_application.labs.terminal import TerminalRegistry
from edu_it_domain import config as _domain_config
from edu_it_infrastructure.composition import CompositionSettings
from fastapi import FastAPI
from pydantic import ValidationError

from edu_it_api.app import create_app
from edu_it_api.composition import ApiAdapters, ApiRouters, build_adapters, build_api_routers
from edu_it_api.health import ProbesProvider
from edu_it_api.startup import WEB_BASE_URL_VARIABLE, refuse_start

__all__ = [
    "DrainingServer",
    "ServedApp",
    "build_served_app",
    "compose_served_app",
    "load_settings",
    "main",
]


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


@dataclass(frozen=True, slots=True)
class ServedApp:
    """The composed Served_App with the adapters and routers it was built from."""

    app: FastAPI
    registry: TerminalRegistry
    adapters: ApiAdapters
    routers: ApiRouters


def compose_served_app(
    settings: CompositionSettings,
    *,
    fakes: bool,
    content_root: str | Path,
    probes_provider: ProbesProvider | None = None,
) -> ServedApp:
    """Build the Served_App from ``settings`` with real adapters or in-memory fakes."""
    adapters = build_adapters(settings, fakes=fakes)
    routers = build_api_routers(adapters, content_root=content_root, config=_domain_config)
    app = create_app(
        probes_provider,
        v1_routers=routers.v1_routers,
        web_base_url=settings.web_base_url,
        allowed_web_origins=settings.allowed_web_origins,
    )
    return ServedApp(app=app, registry=routers.registry, adapters=adapters, routers=routers)


def load_settings() -> CompositionSettings:
    """Read the settings from the environment; an invalid ``WEB_BASE_URL`` refuses the start."""
    try:
        return CompositionSettings.from_env()
    except ValidationError as exc:
        names = sorted({str(e["loc"][0]).upper() for e in exc.errors() if e["loc"]})
        # Name the failing variable only: a pydantic message echoes the offending value.
        if any(name == "WEB_BASE_URL" for name in names):
            raise refuse_start(WEB_BASE_URL_VARIABLE, exc) from exc
        raise refuse_start("settings", exc) from exc


def build_served_app() -> tuple[FastAPI, TerminalRegistry]:
    """Compose the served app: every ``/v1`` router over the real adapters.

    The content root is read from ``EDU_IT_CONTENT_ROOT`` (default ``content``). The
    returned registry is the one the terminal gateway uses, so draining it reaches every
    open terminal.
    """
    served = compose_served_app(
        load_settings(),
        fakes=False,
        content_root=os.environ.get("EDU_IT_CONTENT_ROOT", "content"),
    )
    return served.app, served.registry


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
