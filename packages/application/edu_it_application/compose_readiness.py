"""Framework-free aggregate Compose readiness decision (task 10, Req 3.4-3.6).

The local Docker Compose stack runs five services; three of them — Web_App,
API_Service, and Worker_Service — expose a Readiness_Endpoint. The stack is
considered *ready* only when **all three** of those Readiness_Endpoints report
ready within the 120-second window, and the failure of any single one to become
ready within that window fails the whole check regardless of the others
(Req 3.4, 3.5, 3.6). This is the project's "atomic all-or-nothing readiness".

That aggregate rule is a pure function of, per service, whether it became ready
within the window. It lives here in the application layer — not inside the wait
script's polling loop — so the decision is explicit, observable, and exercised
directly by the Property 35 property test without a live stack. The wait script
(:mod:`edu_it_worker`'s sibling tooling) owns the I/O: it polls the three
endpoints until each reports ready or the window elapses, records the per-service
outcome, and hands those outcomes to :func:`decide_compose_readiness` for the
verdict and failure report.

Two things this module guarantees across every per-service outcome assignment:

- **Passes iff all three ready in time** (Req 3.4, 3.6): the aggregate check
  passes exactly when every one of the three services reported ready within the
  window. One timeout is enough to fail it (Req 3.5), two or three timeouts also
  fail it, and the verdict never depends on the services that *did* become ready.
- **Names the timed-out service(s)** (Req 3.5): when the check fails, the report
  lists exactly the services that did not become ready within the window, so an
  operator (or the phase-1 report) can see which service to investigate without
  any internal connection detail.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

__all__ = [
    "COMPOSE_READINESS_SERVICES",
    "COMPOSE_READINESS_TIMEOUT",
    "ComposeReadinessReport",
    "decide_compose_readiness",
]

#: The aggregate Compose readiness window (Req 3.4-3.6): each of the three
#: services' Readiness_Endpoints must report ready within 120 seconds of the
#: start command, and any single timeout past this window fails the whole check.
#: Kept as a :class:`datetime.timedelta` so callers compare it against elapsed
#: time without unit ambiguity. This is the deployment-wiring deadline fixed by
#: Requirement 3, not a tunable account business number, so it lives beside the
#: aggregate decision it governs rather than in ``domain/config.py``.
COMPOSE_READINESS_TIMEOUT: Final[timedelta] = timedelta(seconds=120)

#: The three Compose services whose Readiness_Endpoints the aggregate check
#: polls, in a stable order (Req 3.4). PostgreSQL and Redis are *not* in this set:
#: they have no Readiness_Endpoint of their own; their reachability is what the
#: api and worker readiness checks already reflect.
COMPOSE_READINESS_SERVICES: Final[tuple[str, ...]] = ("web", "api", "worker")


@dataclass(frozen=True, slots=True)
class ComposeReadinessReport:
    """The outcome of the aggregate Compose readiness check.

    ``passed`` is ``True`` exactly when ``timed_out`` is empty, i.e. when every
    one of the three services reported ready within the window. ``timed_out``
    names the services that did not become ready in time (in a stable order), so
    a failed check can indicate *which* service(s) timed out (Req 3.5) without
    exposing any internal detail.
    """

    passed: bool
    timed_out: tuple[str, ...] = field(default_factory=tuple)


def decide_compose_readiness(
    ready_in_time: Mapping[str, bool] | Iterable[tuple[str, bool]],
) -> ComposeReadinessReport:
    """Decide aggregate Compose readiness from per-service outcomes (Req 3.4-3.6).

    ``ready_in_time`` maps each of the three services
    (:data:`COMPOSE_READINESS_SERVICES`) to whether its Readiness_Endpoint
    reported ready within :data:`COMPOSE_READINESS_TIMEOUT`. The check passes
    exactly when every one of the three mapped to ``True`` (Req 3.4, 3.6); any
    service that mapped to ``False`` is a timeout that fails the whole check
    regardless of the others (Req 3.5), and the returned report names exactly
    those timed-out services in :data:`COMPOSE_READINESS_SERVICES` order.

    The assignment must cover exactly the three services — no more, no fewer — so
    the aggregate verdict is always over the full, fixed service set; a mapping
    that omits a service or names an unknown one raises :class:`ValueError`
    rather than silently deciding over a partial set.
    """
    outcomes = dict(ready_in_time)
    _require_exact_services(outcomes.keys())

    # Report the timed-out services in the canonical service order, not the
    # mapping's iteration order, so the failure report is deterministic.
    timed_out = tuple(service for service in COMPOSE_READINESS_SERVICES if not outcomes[service])
    return ComposeReadinessReport(passed=not timed_out, timed_out=timed_out)


def _require_exact_services(services: Iterable[str]) -> None:
    """Raise :class:`ValueError` unless ``services`` is exactly the three services.

    Guards :func:`decide_compose_readiness` against deciding over a partial or
    extraneous service set: the aggregate rule is defined only over Web_App,
    API_Service, and Worker_Service (Req 3.4), so a missing or unknown service is
    a programming error, surfaced loudly rather than papered over.
    """
    present: Sequence[str] = list(services)
    present_set = set(present)
    expected_set = set(COMPOSE_READINESS_SERVICES)

    if present_set == expected_set and len(present) == len(COMPOSE_READINESS_SERVICES):
        return

    missing = tuple(s for s in COMPOSE_READINESS_SERVICES if s not in present_set)
    unknown = tuple(s for s in present if s not in expected_set)
    raise ValueError(
        "compose readiness outcomes must cover exactly the three services "
        f"{COMPOSE_READINESS_SERVICES!r}; "
        f"missing={missing!r} unknown={unknown!r}"
    )
