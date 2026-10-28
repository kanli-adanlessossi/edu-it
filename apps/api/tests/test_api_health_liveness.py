"""Unit tests for the API Health_Endpoint liveness independence (task 9.2, Req 18.1).

Property 26's liveness clause and Req 18.1 require the Health_Endpoint to report
liveness *independent of external dependencies*. These tests assert that: the
liveness route returns an alive ``200`` no matter what the readiness probes would
say — including when every dependency is unreachable and even when every probe
*raises* on contact. They also pin the contrast: with the same all-unreachable
probes the Readiness_Endpoint reports not-ready, proving liveness and readiness
read dependency state differently (liveness: not at all).

No HTTP client is used (the project pins none): the route callables registered
by :func:`edu_it_api.health.build_health_router` are resolved from the router and
invoked directly, the same technique the task-8 property tests use for the error
handlers.
"""

from __future__ import annotations

import json
from typing import Any

from edu_it_api.health import build_health_router
from edu_it_domain.ports.health import DependencyProbe
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


def _endpoint(path: str, probes: list[DependencyProbe]) -> Any:
    """Return the route callable registered for ``path`` on a router built with ``probes``."""
    router = build_health_router(lambda: probes)
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint  # type: ignore[attr-defined]
    raise AssertionError(f"no route for {path!r}")


def _body(response: Response) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(bytes(response.body))
    return parsed


def test_liveness_is_alive_when_all_dependencies_are_unreachable() -> None:
    response = _endpoint("/health", _UNREACHABLE)()
    assert response.status_code == 200
    assert _body(response) == {"status": "alive"}


def test_liveness_is_alive_when_every_probe_raises() -> None:
    # Liveness must not even *run* the probes; a probe that raises on contact
    # cannot affect it.
    response = _endpoint("/health", _RAISING)()
    assert response.status_code == 200
    assert _body(response) == {"status": "alive"}


def test_readiness_is_not_ready_with_the_same_unreachable_dependencies() -> None:
    # Contrast: readiness *does* reflect dependency reachability, so the same
    # probes that leave liveness alive make readiness not-ready (Req 18.3).
    response = _endpoint("/ready", _UNREACHABLE)()
    assert response.status_code == 503
    body = _body(response)
    assert body["status"] == "not_ready"
    assert set(body["unreachable"]) == {"postgresql", "redis"}


def test_readiness_is_ready_when_all_dependencies_are_reachable() -> None:
    reachable = [
        DependencyProbe(name="postgresql", is_reachable=lambda: True),
        DependencyProbe(name="redis", is_reachable=lambda: True),
    ]
    response = _endpoint("/ready", reachable)()
    assert response.status_code == 200
    assert _body(response) == {"status": "ready"}
