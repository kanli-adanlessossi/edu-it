"""CORS allowlist for direct browser-to-API calls (api-route-wiring Req 2.8, 2.9).

The allowed methods are derived from the route table, never hand-listed, so a method
added to a route (for example ``PUT`` on the lab files route) is allowed in
preflight automatically. Credentials are allowed because the Session_Cookie and the
``csrf_token`` cookie ride on every call. An origin outside the allowlist receives
no CORS headers.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from edu_it_api.route_table import RouteEntry

__all__ = ["ALLOWED_HEADERS", "allowed_methods", "install_cors"]

#: Request headers a browser client may send cross-origin.
ALLOWED_HEADERS: Final[tuple[str, ...]] = (
    "Content-Type",
    "X-CSRF-Token",
    "Idempotency-Key",
    "Accept-Language",
    "Accept",
)


def allowed_methods(route_table: Iterable[RouteEntry]) -> list[str]:
    """Return the sorted HTTP methods in ``route_table`` (WebSocket excluded) plus ``OPTIONS``."""
    return sorted({e.method for e in route_table if e.method != "WS"} | {"OPTIONS"})


def install_cors(app: FastAPI, origins: Sequence[str], route_table: Iterable[RouteEntry]) -> None:
    """Install the CORS layer on ``app``.

    Call this last among ``add_middleware`` calls so CORS is the outermost layer and
    preflights never reach CSRF or request logging.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=True,
        allow_methods=allowed_methods(route_table),
        allow_headers=list(ALLOWED_HEADERS),
    )
