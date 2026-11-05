"""API entrypoint with a draining uvicorn server (Req 12.8, 26.5).

On SIGINT/SIGTERM the stock uvicorn server stops accepting connections and waits
for open ones to finish, which never happens for long-lived terminal WebSockets.
:class:`DrainingServer` overrides ``handle_exit`` so that the first signal first
runs the drain (``DrainCoordinator.start_drain``: readiness ``503``, preStop delay, each
open terminal receives ``{"type":"reconnect"}`` and is closed with 1012, force-close at
the drain period) and only then falls through to uvicorn's normal shutdown. A repeated
signal skips the drain and shuts down immediately. The ``preStop`` hook reaches the same
method through ``GET /internal/drain`` on the admin port (Req 14.1, 14.2, 14.12).

Run with ``python -m edu_it_api.serve``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

import uvicorn
from edu_it_application.labs.terminal import TerminalRegistry
from edu_it_domain import config as _domain_config
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.telemetry import (
    MetricsPortError,
    PrometheusLabMetrics,
    configure_logging,
    configure_tracing_from_env,
    resolve_metrics_port,
    shutdown_tracing,
    start_metrics_server,
)
from fastapi import FastAPI
from pydantic import ValidationError

from edu_it_api.app import create_app
from edu_it_api.composition import ApiAdapters, ApiRouters, build_adapters, build_api_routers
from edu_it_api.drain import DrainCoordinator, build_admin_app, validate_drain_settings
from edu_it_api.health import ProbesProvider
from edu_it_api.startup import WEB_BASE_URL_VARIABLE, refuse_start

if TYPE_CHECKING:
    from wsgiref.simple_server import WSGIServer

    from opentelemetry.sdk.trace import TracerProvider
    from prometheus_client import CollectorRegistry

__all__ = [
    "ApiTelemetry",
    "DrainingServer",
    "ServedApp",
    "build_served_app",
    "compose_served_app",
    "load_settings",
    "main",
    "start_telemetry",
]


class _AdminServer(uvicorn.Server):
    """The admin-port server; it never installs signal handlers (the main server owns them)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class DrainingServer(uvicorn.Server):
    """``uvicorn.Server`` that drains terminal sockets before shutting down.

    With a ``coordinator`` the first signal calls ``DrainCoordinator.start_drain`` (the same
    method the ``preStop`` hook reaches through ``/internal/drain``, Req 14.1). Without
    one it falls back to ``registry.drain``. With an ``admin_config`` the admin app is
    served on its own port for the life of the main server.
    """

    def __init__(
        self,
        config: uvicorn.Config,
        registry: TerminalRegistry,
        coordinator: DrainCoordinator | None = None,
        admin_config: uvicorn.Config | None = None,
    ) -> None:
        super().__init__(config)
        self._registry = registry
        self._coordinator = coordinator
        self._admin = _AdminServer(admin_config) if admin_config is not None else None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._draining = False
        self._drain_task: asyncio.Task[None] | None = None

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        admin_task = asyncio.ensure_future(self._admin.serve()) if self._admin else None
        try:
            await super().serve(sockets)
        finally:
            if self._admin is not None and admin_task is not None:
                self._admin.should_exit = True
                await asyncio.gather(admin_task, return_exceptions=True)

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
            if self._coordinator is not None:
                await self._coordinator.start_drain()
            else:
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
    coordinator: DrainCoordinator | None = None


def compose_served_app(
    settings: CompositionSettings,
    *,
    fakes: bool,
    content_root: str | Path,
    probes_provider: ProbesProvider | None = None,
    drain_period_seconds: int = _domain_config.API_DRAIN_PERIOD_DEFAULT_SECONDS,
    prestop_delay_seconds: int = _domain_config.API_PRESTOP_DELAY_DEFAULT_SECONDS,
) -> ServedApp:
    """Build the Served_App from ``settings`` with real adapters or in-memory fakes.

    The ``DrainCoordinator`` shares the terminal registry and the adapters' clock, and
    readiness answers ``503`` once it is draining (Req 14.1).
    """
    adapters = build_adapters(settings, fakes=fakes)
    routers = build_api_routers(adapters, content_root=content_root, config=_domain_config)
    coordinator = DrainCoordinator(
        routers.registry,
        adapters.clock,
        drain_period_seconds=drain_period_seconds,
        prestop_delay_seconds=prestop_delay_seconds,
    )
    app = create_app(
        probes_provider,
        v1_routers=routers.v1_routers,
        web_base_url=settings.web_base_url,
        allowed_web_origins=settings.allowed_web_origins,
        is_draining=lambda: coordinator.draining,
    )
    return ServedApp(
        app=app,
        registry=routers.registry,
        adapters=adapters,
        routers=routers,
        coordinator=coordinator,
    )


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


def _int_env(name: str, default: int) -> int:
    """Read an integer setting from the environment; a malformed value refuses the start."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise refuse_start(name, exc) from exc


def _compose_from_env() -> ServedApp:
    drain_period = _int_env(
        "API_DRAIN_PERIOD_SECONDS", _domain_config.API_DRAIN_PERIOD_DEFAULT_SECONDS
    )
    prestop_delay = _int_env(
        "API_PRESTOP_DELAY_SECONDS", _domain_config.API_PRESTOP_DELAY_DEFAULT_SECONDS
    )
    try:
        validate_drain_settings(drain_period, prestop_delay)
    except ValueError as exc:
        # An out-of-range drain period or preStop delay refuses the start (Req 14.2, 14.12).
        raise refuse_start("API_DRAIN_PERIOD_SECONDS/API_PRESTOP_DELAY_SECONDS", exc) from exc
    return compose_served_app(
        load_settings(),
        fakes=False,
        content_root=os.environ.get("EDU_IT_CONTENT_ROOT", "content"),
        drain_period_seconds=drain_period,
        prestop_delay_seconds=prestop_delay,
    )


def build_served_app() -> tuple[FastAPI, TerminalRegistry]:
    """Compose the served app: every ``/v1`` router over the real adapters.

    The content root is read from ``EDU_IT_CONTENT_ROOT`` (default ``content``). The
    returned registry is the one the terminal gateway uses, so draining it reaches every
    open terminal.
    """
    served = _compose_from_env()
    return served.app, served.registry


METRICS_PORT_VARIABLE = "API_METRICS_PORT"
METRICS_HOST_VARIABLE = "API_METRICS_HOST"
_DEFAULT_METRICS_PORT = 9090


@dataclass(frozen=True, slots=True)
class ApiTelemetry:
    """The per-process telemetry: the one Prometheus registry and the tracer provider."""

    metrics: PrometheusLabMetrics
    metrics_server: WSGIServer
    tracer_provider: TracerProvider | None

    def shutdown(self) -> None:
        """Stop the metrics endpoint and flush the trace exporter (best effort)."""
        try:
            self.metrics_server.shutdown()
            self.metrics_server.server_close()
        finally:
            shutdown_tracing(self.tracer_provider)


def start_telemetry(
    environ: Mapping[str, str],
    *,
    traffic_ports: Collection[int],
    registry: CollectorRegistry | None = None,
) -> ApiTelemetry:
    """Create the process's one ``PrometheusLabMetrics`` and serve it on the metrics port.

    The metrics port (``API_METRICS_PORT``, default 9090) is separate from the traffic and
    admin ports (Req 13.3); a clash or a malformed value refuses the start. Tracing is
    configured from ``OTEL_EXPORTER_OTLP_ENDPOINT`` and is a no-op without it (Req 13.1).
    """
    try:
        port = resolve_metrics_port(
            environ,
            METRICS_PORT_VARIABLE,
            default=_DEFAULT_METRICS_PORT,
            traffic_ports=traffic_ports,
        )
    except MetricsPortError as exc:
        raise refuse_start(METRICS_PORT_VARIABLE, exc) from exc
    metrics = PrometheusLabMetrics(registry)
    provider = configure_tracing_from_env(environ, service_name="edu-it-api")
    try:
        server, _thread = start_metrics_server(
            port, host=environ.get(METRICS_HOST_VARIABLE, "127.0.0.1"), registry=registry
        )
    except OSError as exc:
        shutdown_tracing(provider)
        raise refuse_start(METRICS_PORT_VARIABLE, exc) from exc
    return ApiTelemetry(metrics=metrics, metrics_server=server, tracer_provider=provider)


def main() -> None:
    """Build the app and serve it, with the admin port, using the draining server."""
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    admin_port = int(os.environ.get("API_ADMIN_PORT", "8081"))
    # Logging first (shared redacting JSON formatter), then the metrics port and tracing.
    configure_logging()
    telemetry = start_telemetry(os.environ, traffic_ports={port, admin_port})
    try:
        served = _compose_from_env()
        assert served.coordinator is not None
        config = uvicorn.Config(served.app, host=host, port=port)
        admin_config = uvicorn.Config(
            build_admin_app(served.coordinator),
            host=os.environ.get("API_ADMIN_HOST", host),
            port=admin_port,
        )
        DrainingServer(config, served.registry, served.coordinator, admin_config).run()
    finally:
        telemetry.shutdown()


if __name__ == "__main__":  # pragma: no cover
    main()
