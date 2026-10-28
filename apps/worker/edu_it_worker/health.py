"""The worker Health_Endpoint and Readiness_Endpoint (task 9, Req 18.1-18.3, 27.1, 27.3).

``apps/worker`` is a task-queue consumer, not an HTTP service, but it must still
expose a Health_Endpoint (liveness) and a Readiness_Endpoint (readiness) so the
orchestrator and the aggregate Compose readiness wait can probe it exactly as
they probe the API (Req 18.1, 18.2). This module is that thin HTTP surface,
built on Starlette (the ASGI toolkit that already backs the API's FastAPI) so the
worker adds no second web stack.

The endpoints report the same decisions as the API's, computed by the same
framework-free logic:

- ``GET /health`` → :func:`edu_it_application.health.check_liveness`: always
  ``200``/alive, independent of dependencies (Req 18.1).
- ``GET /ready`` → :func:`edu_it_application.health.check_readiness` over the
  worker's required-dependency probes (PostgreSQL and Redis): ``200``/ready only
  when all are reachable (Req 18.2), ``503``/not-ready naming the unreachable
  dependencies otherwise (Req 18.3, 27.1). Probes run per request, so readiness
  recovers once dependencies are reachable again (Req 27.3).

The probe set is injected via :func:`build_health_app` so tests drive every
reachable/unreachable assignment without a datastore; the entrypoint wires the
real :func:`edu_it_infrastructure.reachability.required_dependency_probes`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from http import HTTPStatus

from edu_it_application.health import check_liveness, check_readiness
from edu_it_domain.ports.health import DependencyProbe
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

__all__ = ["ProbesProvider", "build_health_app"]

ProbesProvider = Callable[[], Sequence[DependencyProbe]]
"""A zero-argument supplier of the current required-dependency probes.

Fetched per readiness request so a datastore that comes back up is reflected on
the next check (Req 27.3)."""


def build_health_app(probes_provider: ProbesProvider) -> Starlette:
    """Build the worker's ASGI app exposing the liveness and readiness endpoints.

    ``probes_provider`` supplies the readiness probes; it is called on each
    readiness request so readiness reflects current reachability. The liveness
    route ignores it, keeping liveness independent of dependencies (Req 18.1).
    """

    async def health(_request: Request) -> JSONResponse:
        """Liveness: always ``200``/alive, independent of dependencies (Req 18.1)."""
        report = check_liveness()
        return JSONResponse(
            {"status": "alive" if report.alive else "dead"},
            status_code=HTTPStatus.OK,
        )

    async def ready(_request: Request) -> JSONResponse:
        """Readiness: ``200`` only when all required dependencies are reachable.

        Ready (``200``) iff every probe is reachable (Req 18.2); otherwise
        not-ready (``503``) naming the unreachable dependencies (Req 18.3, 27.1).
        Probes run per request, so readiness recovers once dependencies are
        reachable again (Req 27.3).
        """
        report = check_readiness(probes_provider())
        if report.ready:
            return JSONResponse({"status": "ready"}, status_code=HTTPStatus.OK)
        return JSONResponse(
            {"status": "not_ready", "unreachable": list(report.unreachable)},
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/ready", ready, methods=["GET"]),
        ]
    )
