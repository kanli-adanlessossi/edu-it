"""Route-enumeration test: every lab route lives under ``/v1`` (task 7.9).

Walks the real application (mounted sub-apps recursively; HTTP, SSE and
WebSocket routes alike) with all lab services wired, and fails when a lab route
is registered outside ``/v1``. Lab routes are recognised by path segment
(``labs``, ``lab-sessions``, ``terminal``) or the ``labs`` tag, so the terminal
WebSocket is covered automatically once it is added.

**Validates: Requirements 17.2, 17.7**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from edu_it_api.app import create_app
from fastapi import FastAPI
from starlette.routing import BaseRoute, Mount

_SPEC = importlib.util.spec_from_file_location(
    "_labs_routes_env", Path(__file__).with_name("test_labs_routes.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = sys.modules.get(_SPEC.name) or importlib.util.module_from_spec(_SPEC)
if _SPEC.name not in sys.modules:
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
_Env: Any = _MODULE._Env

_LAB_SEGMENTS = frozenset({"labs", "lab-sessions", "terminal"})
_V1 = "/v1"


def _walk(routes: list[BaseRoute], prefix: str = "") -> list[tuple[str, BaseRoute]]:
    """Every leaf route with its full path, recursing into mounted sub-apps."""
    found: list[tuple[str, BaseRoute]] = []
    for route in routes:
        path = getattr(route, "path", "")
        original = getattr(route, "original_router", None)
        if original is not None:
            # Newer FastAPI wraps ``include_router`` results in a path-less route.
            include_prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            found.extend(_walk(list(original.routes), prefix + include_prefix))
            continue
        if isinstance(route, Mount):
            child = getattr(route.app, "routes", None)
            if child is not None:
                found.extend(_walk(list(child), prefix + route.path))
            continue
        found.append((prefix + path, route))
    return found


def _is_lab_route(path: str, route: BaseRoute) -> bool:
    segments = {s for s in path.split("/") if s}
    return bool(segments & _LAB_SEGMENTS) or "labs" in (getattr(route, "tags", None) or [])


def _lab_routes(app: FastAPI) -> list[str]:
    return sorted(p for p, r in _walk(list(app.routes)) if _is_lab_route(p, r))


def _outside_v1(app: FastAPI) -> list[str]:
    return [p for p in _lab_routes(app) if p != _V1 and not p.startswith(_V1 + "/")]


@pytest.fixture
def lab_app() -> FastAPI:
    """The real app with every lab service (incl. grading runs and SSE) wired."""
    return create_app(lab_router=_Env().router)


def test_lab_routes_are_enumerated(lab_app: FastAPI) -> None:
    paths = _lab_routes(lab_app)
    assert "/v1/labs/{lab_id}" in paths
    assert "/v1/lab-sessions" in paths
    assert "/v1/lab-sessions/{session_id}/grading-runs/{run_id}/events" in paths
    assert "/v1/lab-sessions/{session_id}/terminal" in paths


def test_every_lab_route_is_under_v1(lab_app: FastAPI) -> None:
    assert _outside_v1(lab_app) == []


def test_enumerator_detects_lab_routes_outside_v1(lab_app: FastAPI) -> None:
    @lab_app.get("/lab-sessions/leak")
    def _rest() -> None: ...

    @lab_app.websocket("/labs/terminal")
    async def _ws() -> None: ...

    sub = FastAPI()

    @sub.get("/lab-sessions/nested")
    def _nested() -> None: ...

    lab_app.mount("/internal", sub)

    assert sorted(_outside_v1(lab_app)) == [
        "/internal/lab-sessions/nested",
        "/lab-sessions/leak",
        "/labs/terminal",
    ]
