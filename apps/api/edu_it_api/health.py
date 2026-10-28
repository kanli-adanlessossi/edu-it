"""The API Health_Endpoint and Readiness_Endpoint (task 9, Req 18.1-18.3, 27.1, 27.3).

This is the thin ``apps/api`` HTTP boundary over the framework-free health
logic. ``GET /health`` is the Health_Endpoint (liveness) and ``GET /ready`` is
the Readiness_Endpoint (readiness):

- **Liveness** calls :func:`edu_it_application.health.check_liveness`, which
  consults no dependency, and always answers ``200`` with ``{"status": "alive"}``.
  It is independent of PostgreSQL / Redis reachability (Req 18.1): the route
  builds no probes and touches no datastore.
- **Readiness** calls :func:`edu_it_application.health.check_readiness` with the
  required-dependency probes (PostgreSQL and Redis). It answers ``200`` with
  ``{"status": "ready"}`` only when every dependency is reachable (Req 18.2), and
  ``503`` with ``{"status": "not_ready", "unreachable": [...]}`` when one or more
  is unreachable (Req 18.3, 27.1). Because the probes are consulted on each
  request, readiness becomes ready again once PostgreSQL and Redis are reachable
  again (Req 27.3).

The probe set is injected via :func:`build_health_router` so tests can drive
every reachable/unreachable assignment without a live datastore, and production
wires the real :func:`edu_it_infrastructure.reachability.required_dependency_probes`.
Only this readiness route builds probes; liveness never does, which is what keeps
liveness independent of dependencies.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from http import HTTPStatus

from edu_it_application.health import check_liveness, check_readiness
from edu_it_domain.ports.health import DependencyProbe
from fastapi import APIRouter
from fastapi.responses import JSONResponse

__all__ = ["ProbesProvider", "build_health_router"]

ProbesProvider = Callable[[], Sequence[DependencyProbe]]
"""A zero-argument supplier of the current required-dependency probes.

Taking a provider (rather than a fixed probe sequence) keeps readiness honest:
the probes are fetched per request, so a changed set — or a datastore that comes
back up — is reflected on the next readiness check (Req 27.3)."""


def build_health_router(probes_provider: ProbesProvider) -> APIRouter:
    """Build the router exposing the liveness and readiness endpoints.

    ``probes_provider`` supplies the readiness probes for the service's required
    dependencies; it is called on each readiness request so readiness always
    reflects current reachability. The liveness route ignores it entirely,
    keeping liveness independent of dependencies (Req 18.1).
    """
    router = APIRouter(tags=["health"])

    @router.get("/health")
    def health() -> JSONResponse:
        """Liveness: always ``200``/alive, independent of dependencies (Req 18.1)."""
        report = check_liveness()
        return JSONResponse(
            status_code=HTTPStatus.OK,
            content={"status": "alive" if report.alive else "dead"},
        )

    @router.get("/ready")
    def ready() -> JSONResponse:
        """Readiness: ``200`` only when all required dependencies are reachable.

        Reports ready (``200``) iff every probe is reachable (Req 18.2); otherwise
        not-ready (``503``) naming the unreachable dependencies (Req 18.3, 27.1).
        Probes are fetched and run per request, so readiness recovers on its own
        once dependencies are reachable again (Req 27.3).
        """
        report = check_readiness(probes_provider())
        if report.ready:
            return JSONResponse(
                status_code=HTTPStatus.OK,
                content={"status": "ready"},
            )
        return JSONResponse(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "unreachable": list(report.unreachable)},
        )

    return router
