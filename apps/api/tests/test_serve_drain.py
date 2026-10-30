"""Spike test: DrainingServer drains terminals before uvicorn's normal shutdown.

Confirms on the pinned uvicorn that ``handle_exit`` -> ``registry.drain()`` runs
to completion *before* ``should_exit`` is set (Req 12.8, 26.5).
"""

from __future__ import annotations

import asyncio
import signal
import threading

import uvicorn
from edu_it_api.serve import DrainingServer
from edu_it_application.labs.terminal import TerminalRegistry
from fastapi import FastAPI


class _RecordingRegistry(TerminalRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.server: DrainingServer | None = None

    async def drain(self) -> None:
        assert self.server is not None
        self.events.append(f"drain-start should_exit={self.server.should_exit}")
        await asyncio.sleep(0.2)  # a slow drain must still finish first
        self.events.append(f"drain-end should_exit={self.server.should_exit}")


def test_drain_runs_before_normal_shutdown() -> None:
    registry = _RecordingRegistry()
    config = uvicorn.Config(FastAPI(), host="127.0.0.1", port=0, log_level="warning")
    server = DrainingServer(config, registry)
    registry.server = server

    async def scenario() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        server.handle_exit(signal.SIGTERM, None)
        assert server.should_exit is False
        await asyncio.wait_for(task, 10)
        assert server.should_exit is True

    # uvicorn only installs signal handlers on the main thread; run off it so the
    # test never re-raises a captured signal into pytest.
    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(scenario())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(30)
    assert not errors, errors
    assert registry.events == [
        "drain-start should_exit=False",
        "drain-end should_exit=False",
    ]
