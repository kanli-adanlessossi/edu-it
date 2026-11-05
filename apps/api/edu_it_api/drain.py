"""Api drain on shutdown: ``DrainCoordinator`` and the admin ``/internal/drain`` endpoint.

Requirement 14.1, 14.2, 14.3, 14.9 and 14.12; design decision D7.

The coordinator has one idempotent method, :meth:`DrainCoordinator.start_drain`. The
``preStop`` HTTP hook (``GET /internal/drain`` on the admin port) and the SIGTERM
handler both call it, so the sequence never runs twice:

1. ``draining`` becomes true before any wait. Readiness answers ``503`` from then on,
   so it is reported not-ready before the drain starts (Req 14.1).
2. Wait the preStop delay so the removal of the endpoint from the ingress propagates
   (Req 14.12).
3. Stop accepting new terminal upgrades (Req 14.2).
4. Send the reconnect message on every open socket and close it (Req 14.2).
5. At the drain period, force-close any socket that is still open (Req 14.3).

Only sockets are touched. The coordinator holds no sandbox port, so every Lab_Pod and
lab session stays as it is and is reattachable (Req 14.3). Terminal bytes are never
read or stored here (Req 14.9).

Time comes from the injected ``Clock`` for timestamps and from an injected ``sleep``
for waiting. Neither the wall clock nor ``asyncio.sleep`` is read directly in the
sequence, so tests drive it with a virtual clock. Every number comes from
``edu_it_domain.config``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from edu_it_application.labs.terminal import TerminalRegistry
from edu_it_domain import config as _config
from edu_it_domain.ports.clock import Clock
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

__all__ = [
    "DRAIN_PATH",
    "DrainCoordinator",
    "DrainReport",
    "build_admin_app",
    "validate_drain_settings",
]

_LOG = logging.getLogger("edu_it.api.drain")

#: Path of the admin drain endpoint used by the ``preStop`` hook.
DRAIN_PATH = "/internal/drain"

Sleep = Callable[[float], Awaitable[None]]


def validate_drain_settings(drain_period_seconds: int, prestop_delay_seconds: int) -> None:
    """Reject a drain period or preStop delay outside the configured bounds.

    Raises:
        ValueError: naming the offending value and its allowed range.
    """
    low, high = _config.API_DRAIN_PERIOD_BOUNDS_SECONDS
    if not low <= drain_period_seconds <= high:
        raise ValueError(f"drain_period_seconds={drain_period_seconds} is outside {low}..{high}")
    low, high = _config.API_PRESTOP_DELAY_BOUNDS_SECONDS
    if not low <= prestop_delay_seconds <= high:
        raise ValueError(f"prestop_delay_seconds={prestop_delay_seconds} is outside {low}..{high}")


@dataclass(frozen=True, slots=True)
class DrainReport:
    """What one drain did, for logs and tests."""

    started_at: datetime
    """When ``start_drain`` was first called (readiness already ``503``)."""
    drain_started_at: datetime
    """When the preStop delay ended and the sockets were told to reconnect."""
    finished_at: datetime
    forced: bool
    """``True`` when the drain period elapsed and sockets were force-closed."""


class DrainCoordinator:
    """Idempotent drain of the terminal sockets (see the module docstring)."""

    def __init__(
        self,
        registry: TerminalRegistry,
        clock: Clock,
        *,
        drain_period_seconds: int = _config.API_DRAIN_PERIOD_DEFAULT_SECONDS,
        prestop_delay_seconds: int = _config.API_PRESTOP_DELAY_DEFAULT_SECONDS,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        validate_drain_settings(drain_period_seconds, prestop_delay_seconds)
        self._registry = registry
        self._clock = clock
        self._drain_period = drain_period_seconds
        self._prestop_delay = prestop_delay_seconds
        self._sleep = sleep
        self._draining = False
        self._task: asyncio.Task[DrainReport] | None = None

    @property
    def draining(self) -> bool:
        """``True`` from the first ``start_drain`` call; readiness reports ``503``."""
        return self._draining

    @property
    def report(self) -> DrainReport | None:
        """The finished drain's report, or ``None`` while it has not finished."""
        task = self._task
        if task is not None and task.done() and not task.cancelled() and task.exception() is None:
            return task.result()
        return None

    async def start_drain(self) -> DrainReport:
        """Run the drain once and return its report; later and concurrent calls share it."""
        if self._task is None:
            # Set before the first await so readiness is already 503 (Req 14.1).
            self._draining = True
            self._task = asyncio.ensure_future(self._run())
        # Shield so a cancelled caller (a closed hook request) does not abort the drain.
        return await asyncio.shield(self._task)

    async def _run(self) -> DrainReport:
        started_at = self._clock.now()
        _LOG.info("drain started; readiness is not ready")
        await self._sleep(self._prestop_delay)

        self._registry.stop_accepting()
        drain_started_at = self._clock.now()
        drain = asyncio.ensure_future(self._registry.drain())
        deadline = asyncio.ensure_future(self._sleep(self._drain_period))
        try:
            await asyncio.wait({drain, deadline}, return_when=asyncio.FIRST_COMPLETED)
            forced = not drain.done()
            if forced:
                drain.cancel()
                await self._registry.force_close_all()
        finally:
            deadline.cancel()
            drain.cancel()
            await asyncio.gather(drain, deadline, return_exceptions=True)
        if not forced and drain.exception() is not None:
            # A failed drain still ends with every socket closed.
            await self._registry.force_close_all()
            forced = True

        report = DrainReport(started_at, drain_started_at, self._clock.now(), forced)
        _LOG.info("drain finished forced=%s", forced)
        return report


def build_admin_app(coordinator: DrainCoordinator) -> FastAPI:
    """Build the admin app served on the admin port (never on the traffic port).

    ``GET /internal/drain`` is what the ``preStop`` ``httpGet`` calls. It answers once the
    drain has finished, so the kubelet sends SIGTERM after the sockets are closed. The
    SIGTERM handler calls the same ``start_drain``.
    """
    router = APIRouter(tags=["admin"])

    @router.get(DRAIN_PATH)
    async def drain() -> JSONResponse:
        report = await coordinator.start_drain()
        return JSONResponse(
            status_code=HTTPStatus.OK,
            content={"status": "drained", "forced": report.forced},
        )

    app = FastAPI(title="EDU-IT API admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    return app
