#!/usr/bin/env python3
"""Aggregate Compose readiness wait script (task 10, Req 3.4-3.6).

The local Docker Compose stack (``deploy/compose/docker-compose.yml``) starts
five services; three of them — Web_App, API_Service, Worker_Service — expose a
Readiness_Endpoint. This script is the **dedicated aggregate readiness wait**
the foundations design calls for: it polls those three Readiness_Endpoints and
passes only when all three report ready within 120 seconds of being started,
failing the whole check on any single timeout and naming the service that did
not become ready (Req 3.4, 3.5, 3.6). It is the command the phase-1 report and
CI invoke to assert the stack came up; it is *not* a per-container healthcheck,
so the all-or-nothing aggregate semantics are explicit and observable.

Design split (I/O here, decision in the application layer):

- This script owns only the I/O and the clock: for each of the three services it
  polls ``GET <base-url>/ready`` until the endpoint answers ``200``/ready or the
  120-second window elapses, and records per service whether it became ready in
  time.
- The *verdict* — pass iff all three were ready in time, otherwise fail and name
  the timed-out service(s) — is the pure
  :func:`edu_it_application.compose_readiness.decide_compose_readiness`, so the
  aggregate rule is tested by the Property 35 property test without a live stack.

Exit status (Req 3.5, 3.6): the script exits ``0`` only when all three services
reported ready within the window; if any one timed out it exits non-zero and
prints which service(s) did not become ready. The service base URLs come from
``--web-url`` / ``--api-url`` / ``--worker-url`` (defaulting to the Compose
host-published ports), so the same script works locally and in CI.

Usage::

    python deploy/compose/wait_for_ready.py \
        [--web-url URL] [--api-url URL] [--worker-url URL] \
        [--timeout SECONDS] [--poll-interval SECONDS]
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus

from edu_it_application.compose_readiness import (
    COMPOSE_READINESS_SERVICES,
    COMPOSE_READINESS_TIMEOUT,
    decide_compose_readiness,
)

# Default host-published base URLs for the three services' Readiness_Endpoints,
# matching the ports the Compose stack publishes (web 3000, api 8000, worker's
# health surface 8001). Overridable per service on the command line so the same
# script runs against a remote or re-mapped stack in CI.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "web": "http://localhost:3000",
    "api": "http://localhost:8000",
    "worker": "http://localhost:8001",
}

# The Readiness_Endpoint path every service exposes (see apps/api and
# apps/worker health routers, task 9).
_READY_PATH = "/ready"


@dataclass(frozen=True, slots=True)
class _Config:
    """Resolved runtime configuration for one wait invocation."""

    base_urls: dict[str, str]
    timeout_seconds: float
    poll_interval_seconds: float


ReadinessProbe = Callable[[str], bool]
"""A one-shot readiness probe: given a service base URL, return whether its
Readiness_Endpoint currently answers ready. Injected so tests and callers can
substitute the HTTP call; the default is :func:`_http_ready`."""


def _http_ready(base_url: str) -> bool:
    """Return whether ``<base_url>/ready`` currently answers ``200``/ready.

    Any non-200 status (including the ``503`` a not-ready endpoint returns) or
    any connection error means not-ready; the error is swallowed so a single
    failed poll never aborts the wait — the loop simply retries until the window
    elapses.
    """
    url = f"{base_url.rstrip('/')}{_READY_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return int(response.status) == HTTPStatus.OK
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_for_service(
    base_url: str,
    deadline: float,
    poll_interval: float,
    probe: ReadinessProbe,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """Poll one service until it reports ready or ``deadline`` passes.

    Returns ``True`` as soon as ``probe`` reports ready, or ``False`` once the
    monotonic clock reaches ``deadline`` without a ready result. The first poll
    runs immediately so an already-ready service returns without waiting a full
    interval.
    """
    while True:
        if probe(base_url):
            return True
        if now() >= deadline:
            return False
        sleep(min(poll_interval, max(0.0, deadline - now())))


def wait_for_compose_ready(
    config: _Config,
    *,
    probe: ReadinessProbe | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, tuple[str, ...]]:
    """Poll the three Readiness_Endpoints and decide aggregate readiness.

    Each of the three services (:data:`COMPOSE_READINESS_SERVICES`) is polled
    until it reports ready or the shared 120-second window elapses; the
    per-service "ready in time" outcomes are then handed to
    :func:`decide_compose_readiness` for the all-or-nothing verdict (Req 3.4-3.6).

    Returns ``(passed, timed_out)`` where ``passed`` is ``True`` only when all
    three were ready in time and ``timed_out`` names the services that were not.
    The ``probe`` / ``now`` / ``sleep`` seams are injected so the polling loop is
    exercisable without real time or a live stack; ``probe`` defaults to the real
    HTTP readiness probe, resolved at call time so callers can substitute it.
    """
    probe = _http_ready if probe is None else probe
    deadline = now() + config.timeout_seconds
    ready_in_time = {
        service: _wait_for_service(
            config.base_urls[service],
            deadline,
            config.poll_interval_seconds,
            probe,
            now,
            sleep,
        )
        for service in COMPOSE_READINESS_SERVICES
    }
    report = decide_compose_readiness(ready_in_time)
    return report.passed, report.timed_out


def _parse_args(argv: Sequence[str]) -> _Config:
    """Parse command-line arguments into a resolved :class:`_Config`."""
    parser = argparse.ArgumentParser(
        prog="wait_for_ready.py",
        description=(
            "Aggregate Compose readiness wait: pass only when the web, api, and "
            "worker Readiness_Endpoints all report ready within the window; fail "
            "and name any service that times out (Req 3.4-3.6)."
        ),
    )
    parser.add_argument("--web-url", default=_DEFAULT_BASE_URLS["web"])
    parser.add_argument("--api-url", default=_DEFAULT_BASE_URLS["api"])
    parser.add_argument("--worker-url", default=_DEFAULT_BASE_URLS["worker"])
    parser.add_argument(
        "--timeout",
        type=float,
        default=COMPOSE_READINESS_TIMEOUT.total_seconds(),
        help="aggregate readiness window in seconds (default: 120)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="seconds between readiness polls (default: 2)",
    )
    args = parser.parse_args(argv)
    return _Config(
        base_urls={
            "web": args.web_url,
            "api": args.api_url,
            "worker": args.worker_url,
        },
        timeout_seconds=args.timeout,
        poll_interval_seconds=args.poll_interval,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the aggregate wait and return a process exit status (Req 3.5, 3.6)."""
    config = _parse_args(sys.argv[1:] if argv is None else argv)
    passed, timed_out = wait_for_compose_ready(config)
    if passed:
        print(
            "compose readiness: PASS — web, api, and worker all reported ready "
            f"within {config.timeout_seconds:.0f}s"
        )
        return 0
    print(
        "compose readiness: FAIL — timed out waiting for: "
        f"{', '.join(timed_out)} "
        f"(window {config.timeout_seconds:.0f}s)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
