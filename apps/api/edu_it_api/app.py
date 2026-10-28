"""FastAPI application factory for the EDU-IT API boundary.

:func:`create_app` builds the FastAPI application, installs structured JSON
logging (Req 18.4), wires the request-logging middleware that binds a
Request_ID to every request (Req 18.4, 18.5), installs the CSRF protection
middleware that gates every unsafe mutation on a valid CSRF_Token (Req 11), and
installs the RFC 9457 Problem_Details exception handlers so every non-2xx
response is serialized as an ``application/problem+json`` document with
locale-rendered text and no internal diagnostics (Req 19), and mounts one
versioned sub-application per supported major version so the service publishes
exactly one OpenAPI_Document per major version, path-prefixed ``/v{major}``
(Req 16.1). It is intentionally thin: feature routers are added by their own
tasks.
"""

from __future__ import annotations

from collections.abc import Sequence

from edu_it_domain.ports.health import DependencyProbe
from edu_it_infrastructure.reachability import required_dependency_probes
from fastapi import FastAPI

from edu_it_api import __version__
from edu_it_api.csrf import CsrfProtectionMiddleware
from edu_it_api.errors import install_error_handlers
from edu_it_api.health import ProbesProvider, build_health_router
from edu_it_api.logging import configure_logging
from edu_it_api.middleware import RequestLoggingMiddleware
from edu_it_api.versioning import mount_version_apps

__all__ = ["create_app"]


def create_app(probes_provider: ProbesProvider | None = None) -> FastAPI:
    """Build and return the configured FastAPI application.

    ``probes_provider`` supplies the readiness probes for the API's required
    dependencies (PostgreSQL and Redis). It defaults to the infrastructure probe
    builder, which constructs its datastore clients lazily, so building the app
    never contacts a datastore — an unreachable datastore at startup simply makes
    the Readiness_Endpoint report not-ready (Req 27.1) while liveness stays up.
    A test passes its own provider to drive every reachable/unreachable
    assignment without a live datastore.
    """
    configure_logging()

    app = FastAPI(title="EDU-IT API", version=__version__)
    # Starlette runs the most-recently-added middleware outermost, so adding the
    # CSRF guard first and the request logger second makes logging the outer
    # layer: a Request_ID is bound (and the access record emitted) even for a
    # request the CSRF guard rejects before it reaches a route (Req 11.2, 18.4).
    app.add_middleware(CsrfProtectionMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    install_error_handlers(app)
    app.include_router(build_health_router(probes_provider or _default_probes_provider))
    # Mount one versioned sub-app per supported major version so the service
    # publishes exactly one OpenAPI_Document per major version at
    # /v{major}/openapi.json (Req 16.1). Feature routers are registered on their
    # version's sub-app by their own tasks.
    mount_version_apps(app)
    return app


def _default_probes_provider() -> Sequence[DependencyProbe]:
    """Return the production required-dependency probes (PostgreSQL and Redis).

    Built from the environment-configured composition settings; the datastore
    clients are created lazily on first probe, so this contacts nothing until a
    readiness request actually runs the probes.
    """
    return required_dependency_probes()
