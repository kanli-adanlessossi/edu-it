"""The worker ASGI application factory for its health/readiness surface (task 9).

:func:`create_health_app` builds the worker's Starlette app exposing the
Health_Endpoint and Readiness_Endpoint (Req 18.1-18.3). The worker's main job is
consuming the task queue; this small HTTP surface exists solely so the
orchestrator and the aggregate Compose readiness wait can probe the worker's
liveness and readiness the same way they probe the API.

Readiness probes are built lazily from the environment-configured composition
settings, so constructing the app contacts no datastore — an unreachable
PostgreSQL or Redis at startup makes readiness report not-ready (Req 27.1) while
liveness stays up, and readiness recovers once they are reachable again
(Req 27.3). A test passes its own provider to :func:`edu_it_worker.health.build_health_app`
directly to drive every reachable/unreachable assignment without a datastore.
"""

from __future__ import annotations

from collections.abc import Sequence

from edu_it_domain.ports.health import DependencyProbe
from edu_it_infrastructure.reachability import required_dependency_probes
from starlette.applications import Starlette

from edu_it_worker.health import build_health_app

__all__ = ["create_health_app"]


def create_health_app() -> Starlette:
    """Build the worker health/readiness ASGI app wired to the real probes."""
    return build_health_app(_default_probes_provider)


def _default_probes_provider() -> Sequence[DependencyProbe]:
    """Return the production required-dependency probes (PostgreSQL and Redis).

    The datastore clients are created lazily on first probe, so this contacts
    nothing until a readiness request runs the probes.
    """
    return required_dependency_probes()
