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

import logging
from collections.abc import Sequence

from edu_it_domain import config as _domain_config
from edu_it_domain.config import LAB_ALLOWED_WEB_ORIGINS
from edu_it_domain.labs.config_rules import LabConfigError, validate_lab_config
from edu_it_domain.ports.health import DependencyProbe
from edu_it_infrastructure.reachability import required_dependency_probes
from fastapi import APIRouter, FastAPI

from edu_it_api import __version__
from edu_it_api.cors import install_cors
from edu_it_api.csrf import CsrfProtectionMiddleware
from edu_it_api.errors import install_error_handlers
from edu_it_api.health import ProbesProvider, build_health_router
from edu_it_api.logging import configure_logging
from edu_it_api.middleware import RequestLoggingMiddleware
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_api.startup import validate_web_base_url
from edu_it_api.versioning import mount_version_apps

__all__ = ["create_app"]


def create_app(
    probes_provider: ProbesProvider | None = None,
    learning_router: APIRouter | None = None,
    lab_config: object = _domain_config,
    lab_router: APIRouter | None = None,
    *,
    v1_routers: Sequence[APIRouter] | None = None,
    web_base_url: str | None = None,
    allowed_web_origins: Sequence[str] = LAB_ALLOWED_WEB_ORIGINS,
) -> FastAPI:
    """Build and return the configured FastAPI application.

    ``probes_provider`` supplies the readiness probes for the API's required
    dependencies (PostgreSQL and Redis). It defaults to the infrastructure probe
    builder, which constructs its datastore clients lazily, so building the app
    never contacts a datastore — an unreachable datastore at startup simply makes
    the Readiness_Endpoint report not-ready (Req 27.1) while liveness stays up.
    A test passes its own provider to drive every reachable/unreachable
    assignment without a live datastore.

    ``v1_routers`` are mounted on the ``/v1`` sub-application; ``learning_router`` and
    ``lab_router`` stay accepted as aliases and are appended after them. When
    ``web_base_url`` is given it must be an absolute http(s) URL, otherwise startup is
    refused (api-route-wiring Req 8.6). ``allowed_web_origins`` feeds the CORS layer.
    """
    configure_logging()
    # Validate the lab Config before composing anything that could reach a
    # provider (Req 1.8, 1.11). A LabConfigError propagates: the API refuses to
    # start, the offending keys are logged, and no Sandbox is created.
    _validate_lab_config_or_refuse(lab_config)
    if web_base_url is not None:
        validate_web_base_url(web_base_url)

    app = FastAPI(title="EDU-IT API", version=__version__)
    # Starlette runs the most-recently-added middleware outermost. CSRF is added first,
    # request logging second and CORS last, so from the outside in the order is CORS,
    # request logging, CSRF: a preflight is answered before CSRF sees it, and a
    # Request_ID is bound (and the access record emitted) even for a request the CSRF
    # guard rejects before it reaches a route (Req 11.2, 18.4; api-route-wiring Req 2.8).
    app.add_middleware(CsrfProtectionMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    install_cors(app, allowed_web_origins, ROUTE_TABLE)
    install_error_handlers(app)
    app.include_router(build_health_router(probes_provider or _default_probes_provider))
    # Mount one versioned sub-app per supported major version so the service
    # publishes exactly one OpenAPI_Document per major version at
    # /v{major}/openapi.json (Req 16.1). Feature routers are registered on their
    # version's sub-app by their own tasks.
    mounted = [
        *(v1_routers or ()),
        *(r for r in (learning_router, lab_router) if r is not None),
    ]
    mount_version_apps(app, {1: mounted} if mounted else None)
    return app


def _validate_lab_config_or_refuse(lab_config: object) -> None:
    """Validate the lab Config; log the offending keys and re-raise on failure."""
    try:
        validate_lab_config(lab_config)
    except LabConfigError as exc:
        logging.getLogger(__name__).critical(
            "refusing to start: invalid lab Config", extra={"keys": list(exc.keys)}
        )
        raise


def _default_probes_provider() -> Sequence[DependencyProbe]:
    """Return the production required-dependency probes (PostgreSQL and Redis).

    Built from the environment-configured composition settings; the datastore
    clients are created lazily on first probe, so this contacts nothing until a
    readiness request actually runs the probes.
    """
    return required_dependency_probes()
