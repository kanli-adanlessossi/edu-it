"""Unit tests for the worker Health_Endpoint liveness independence (task 9.2, Req 18.1).

The worker exposes the same Health_Endpoint / Readiness_Endpoint as the API, so
Req 18.1 applies to it too: liveness must report alive independent of external
dependencies. These tests assert the worker's liveness route returns an alive
``200`` even when every required dependency is unreachable or every probe raises,
and pin the contrast that readiness *does* reflect reachability (not-ready with
the same unreachable probes).

No HTTP client is used (the project pins none, and Starlette's TestClient needs
httpx): the route callables registered by
:func:`edu_it_worker.health.build_health_app` are resolved from the app's routes
and awaited directly over a minimal Starlette ``Request``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from edu_it_domain.ports.health import DependencyProbe
from edu_it_worker.health import build_health_app
from starlette.requests import Request
from starlette.responses import Response

_UNREACHABLE = [
    DependencyProbe(name="postgresql", is_reachable=lambda: False),
    DependencyProbe(name="redis", is_reachable=lambda: False),
]


def _raising() -> bool:
    raise ConnectionError("datastore down")


_RAISING = [
    DependencyProbe(name="postgresql", is_reachable=_raising),
    DependencyProbe(name="redis", is_reachable=_raising),
]


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


def _call(path: str, probes: list[DependencyProbe]) -> Response:
    """Await the route callable registered for ``path`` with ``probes``."""
    app = build_health_app(lambda: probes)
    for route in app.routes:
        if getattr(route, "path", None) == path:
            endpoint = route.endpoint  # type: ignore[attr-defined]
            response: Response = asyncio.run(endpoint(_make_request()))
            return response
    raise AssertionError(f"no route for {path!r}")


def _body(response: Response) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(bytes(response.body))
    return parsed


def test_liveness_is_alive_when_all_dependencies_are_unreachable() -> None:
    response = _call("/health", _UNREACHABLE)
    assert response.status_code == 200
    assert _body(response) == {"status": "alive"}


def test_liveness_is_alive_when_every_probe_raises() -> None:
    response = _call("/health", _RAISING)
    assert response.status_code == 200
    assert _body(response) == {"status": "alive"}


def test_readiness_is_not_ready_with_the_same_unreachable_dependencies() -> None:
    response = _call("/ready", _UNREACHABLE)
    assert response.status_code == 503
    body = _body(response)
    assert body["status"] == "not_ready"
    assert set(body["unreachable"]) == {"postgresql", "redis"}


def test_readiness_is_ready_when_all_dependencies_are_reachable() -> None:
    reachable = [
        DependencyProbe(name="postgresql", is_reachable=lambda: True),
        DependencyProbe(name="redis", is_reachable=lambda: True),
    ]
    response = _call("/ready", reachable)
    assert response.status_code == 200
    assert _body(response) == {"status": "ready"}
