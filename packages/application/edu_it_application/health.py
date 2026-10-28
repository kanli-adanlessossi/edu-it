"""Framework-free liveness and readiness evaluation (task 9, Req 18.1-18.3, 27.1, 27.3).

Both boundaries — ``apps/api`` and ``apps/worker`` — expose a Health_Endpoint
(liveness) and a Readiness_Endpoint (readiness). The *decision* each endpoint
reports is the same pure function of the service's dependency reachability, so
it lives here in the application layer rather than being re-implemented behind
each framework. The HTTP boundaries (:mod:`edu_it_api.health`,
:mod:`edu_it_worker.health`) only translate a request into a call to these
functions and the resulting report into a status code and body.

The two rules this module encodes:

- **Liveness is independent of external dependencies** (Req 18.1). :func:`check_liveness`
  takes no dependency input and always reports alive: it answers only "is this
  process running and able to respond", which is true by virtue of the handler
  executing. A down PostgreSQL or Redis never changes liveness.
- **Readiness is ready exactly when every required dependency is reachable**
  (Req 18.2, 18.3, 27.1, 27.3). :func:`check_readiness` probes each required
  dependency and reports ready if and only if *all* of them are reachable; if one
  or more is unreachable it reports not-ready and names the unreachable
  dependencies. Because readiness is recomputed from the probes on each call,
  a dependency that goes down makes the next readiness check not-ready, and once
  PostgreSQL and Redis are reachable again the next check reports ready again
  (Req 27.3) — the function holds no cached verdict.

A dependency is modelled as a
:class:`~edu_it_domain.ports.health.DependencyProbe` (defined in the domain so
both this layer and the infrastructure probe builders can depend on it): a name
plus a zero-argument callable that returns whether that dependency is currently
reachable. A probe that raises is treated as *unreachable* (a dependency that
errors on contact is not reachable), never propagating the error out of the
readiness check — so probing a down datastore yields a clean not-ready report
rather than an exception.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from edu_it_domain.ports.health import DependencyProbe

__all__ = [
    "LivenessReport",
    "ReadinessReport",
    "check_liveness",
    "check_readiness",
]


@dataclass(frozen=True, slots=True)
class LivenessReport:
    """The outcome of a liveness check.

    ``alive`` is always ``True``: liveness is determined independent of external
    dependencies (Req 18.1), so a running process that can produce this report is
    by definition alive.
    """

    alive: bool = True


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """The outcome of a readiness check.

    ``ready`` is ``True`` exactly when ``unreachable`` is empty, i.e. when every
    required dependency was reachable. ``unreachable`` names the dependencies
    that were not reachable (in the order they were probed), so a not-ready
    response can indicate *which* dependencies are down without exposing any
    internal connection detail.
    """

    ready: bool
    unreachable: tuple[str, ...] = field(default_factory=tuple)


def check_liveness() -> LivenessReport:
    """Report process liveness, independent of any external dependency (Req 18.1).

    Always returns an alive report: the fact that this call executes means the
    process is running and able to respond, which is all liveness asserts. No
    dependency is consulted, so liveness never changes with PostgreSQL or Redis
    reachability.
    """
    return LivenessReport(alive=True)


def check_readiness(probes: Iterable[DependencyProbe]) -> ReadinessReport:
    """Report readiness: ready iff every required dependency is reachable (Req 18.2, 18.3).

    Each probe in ``probes`` is invoked once; a probe returning a falsy value —
    or raising — marks its dependency unreachable. The report is ready exactly
    when no dependency is unreachable (Req 18.2); otherwise it is not-ready and
    lists every unreachable dependency's name (Req 18.3, 27.1). The verdict is
    computed fresh from the probes on each call and nothing is cached, so when
    the dependencies become reachable again a subsequent call reports ready
    again (Req 27.3).

    A service with no required dependencies is always ready (the empty
    conjunction is true).
    """
    probe_list: Sequence[DependencyProbe] = list(probes)
    unreachable = tuple(probe.name for probe in probe_list if not _probe_reachable(probe))
    return ReadinessReport(ready=not unreachable, unreachable=unreachable)


def _probe_reachable(probe: DependencyProbe) -> bool:
    """Return whether ``probe``'s dependency is reachable, treating any error as unreachable.

    A dependency whose reachability check raises (e.g. a connection refused while
    contacting a down datastore) is unreachable by definition; swallowing the
    error here keeps :func:`check_readiness` total — it always returns a report
    and never leaks an internal exception out of the readiness path.
    """
    try:
        return bool(probe.is_reachable())
    except Exception:
        return False
