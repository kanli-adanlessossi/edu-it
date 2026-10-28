"""The dependency-reachability probe port for readiness (Req 18.2, 18.3, 27.1, 27.3).

A service's Readiness_Endpoint reports ready only when every required dependency
is reachable (Req 18.2). A required dependency enters the domain the same way
every other external system does — behind a framework-free port — so the
readiness logic can be evaluated and tested without any real datastore.

A :class:`DependencyProbe` is that port: a named, zero-argument
"is this reachable right now" check. The application layer's readiness evaluation
(:func:`edu_it_application.health.check_readiness`) consumes a set of these
probes; the infrastructure layer supplies concrete probes for PostgreSQL and
Redis; and tests supply trivial callables, so readiness is exercised across every
reachable/unreachable assignment with no server. The probe is a plain dataclass
rather than a ``Protocol`` because it carries data (the name) alongside the
callable, and it imports no framework, keeping the domain boundary intact
(Req 1.8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["DependencyProbe"]


@dataclass(frozen=True, slots=True)
class DependencyProbe:
    """A named reachability check for one required dependency.

    ``is_reachable`` is a zero-argument callable returning ``True`` when the
    dependency can currently be reached. Readiness evaluation calls it afresh on
    every check, so the verdict always reflects the dependency's *current* state
    and is never cached; this is what lets readiness flip to not-ready when a
    dependency goes down and back to ready once it is reachable again (Req 27.3).
    An implementation may raise to signal unreachable — the readiness evaluation
    treats a raising probe as unreachable.
    """

    name: str
    is_reachable: Callable[[], bool]
