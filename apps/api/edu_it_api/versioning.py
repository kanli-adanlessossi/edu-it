"""Per-major-version OpenAPI publication for the EDU-IT API (task 22, Req 16.1).

`docs/API-CONVENTIONS.md` (D2) Section 3 versions the HTTP API with a **major
version in the URL path** — ``/v{major}`` — and states that the OpenAPI document
is published *per major version* so ``apps/web`` can generate a typed client
pinned to that version.

This module realises that convention. Each supported major version is a mounted
sub-:class:`fastapi.FastAPI` application reachable under its ``/v{major}`` path
prefix. Because every FastAPI app owns exactly one OpenAPI schema served at its
own ``openapi_url``, mounting one sub-app per major version yields **exactly one
OpenAPI_Document per major version** (Req 16.1): for major version ``1`` the
document is served at ``/v1/openapi.json`` and the interactive docs at
``/v1/docs``.

Feature routers for a major version are registered on that version's sub-app by
their own tasks; this module only establishes the versioned mount points and the
single-document-per-version guarantee. The top-level application keeps its own
root OpenAPI document for the unversioned operational surface (health/readiness),
which is not part of the versioned public API contract.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, FastAPI

from edu_it_api import __version__

__all__ = [
    "SUPPORTED_MAJOR_VERSIONS",
    "build_version_app",
    "mount_version_apps",
    "version_path_prefix",
]

SUPPORTED_MAJOR_VERSIONS: tuple[int, ...] = (1,)
"""The API major versions published by this service.

Clients pin to a major version (D2 Section 3). The major version increments only
for breaking changes; backward-compatible changes are made within the current
major version. Exactly one OpenAPI_Document is published per entry here.
"""


def version_path_prefix(major: int) -> str:
    """Return the URL path prefix for a major version, e.g. ``/v1`` (D2 §3)."""
    return f"/v{major}"


def build_version_app(major: int, routers: Sequence[APIRouter] | None = None) -> FastAPI:
    """Build the sub-application that serves major version ``major``.

    The returned app owns a single OpenAPI schema (served at its ``openapi_url``)
    describing exactly that major version's contract (Req 16.1). ``routers`` are
    the feature routers that belong to this major version; they are registered
    here so they appear in this version's — and only this version's — document.
    """
    app = FastAPI(
        title=f"EDU-IT API v{major}",
        version=__version__,
        # Path-relative to the mount, so the document is published at
        # /v{major}/openapi.json and the docs at /v{major}/docs (D2 §3, Req 16.1).
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    for router in routers or ():
        app.include_router(router)
    return app


def mount_version_apps(
    app: FastAPI,
    routers_by_major: dict[int, Sequence[APIRouter]] | None = None,
) -> None:
    """Mount one versioned sub-app per supported major version onto ``app``.

    After this call ``app`` exposes, for each supported major version ``m``, a
    sub-application at ``/v{m}`` whose single OpenAPI_Document is served at
    ``/v{m}/openapi.json`` (Req 16.1). ``routers_by_major`` optionally supplies
    the feature routers to register on each version's sub-app.
    """
    routers_by_major = routers_by_major or {}
    for major in SUPPORTED_MAJOR_VERSIONS:
        version_app = build_version_app(major, routers_by_major.get(major))
        app.mount(version_path_prefix(major), version_app)
