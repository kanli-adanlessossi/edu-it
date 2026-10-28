"""Property-based test for readiness reflecting dependency reachability (task 9.1).

Feature: foundations, Property 26: Readiness is ready exactly when all required dependencies are reachable

Property 26 (design.md): *For any* assignment of reachable or unreachable to a
service's required dependencies, its Readiness_Endpoint reports ready if and only
if every required dependency is reachable, and it reports ready again once all
required dependencies become reachable again; the Health_Endpoint reports
liveness independent of dependency reachability.

This drives the framework-free readiness/liveness evaluation
(:func:`edu_it_application.health.check_readiness` /
:func:`edu_it_application.health.check_liveness`) directly — the same functions
both the API and worker boundaries call — so the property holds for whatever
reachable/unreachable assignment Hypothesis draws, with no HTTP server or live
datastore. Each drawn assignment maps a named dependency to a boolean, and each
dependency is realized as a :class:`~edu_it_domain.ports.health.DependencyProbe`
whose callable returns that boolean.

The property asserts three things across every assignment:

1. **Readiness iff all reachable** (Req 18.2, 18.3): the report is ready exactly
   when every dependency is reachable, and when not ready it names exactly the
   unreachable dependencies (Req 27.1).
2. **Recovery** (Req 27.3): flipping every dependency reachable again makes a
   fresh check report ready again — readiness caches no verdict.
3. **Liveness independence** (Req 18.1): liveness is alive for every assignment,
   including when every dependency is unreachable.

Validates: Requirements 18.2, 18.3, 27.1, 27.3

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from edu_it_application.health import check_liveness, check_readiness
from edu_it_domain.ports.health import DependencyProbe
from hypothesis import given, settings
from hypothesis import strategies as st

# A plausible set of required-dependency names a service might have; the two
# real ones (PostgreSQL, Redis) plus extras so the property covers services with
# more than two required dependencies, not just the datastore pair.
_DEPENDENCY_NAMES = ["postgresql", "redis", "object_storage", "smtp", "queue"]


def _constant_probe(name: str, value: bool) -> DependencyProbe:
    """Build a probe for ``name`` whose reachability check returns ``value``."""

    def is_reachable() -> bool:
        return value

    return DependencyProbe(name=name, is_reachable=is_reachable)


def _probes_for(reachability: dict[str, bool]) -> list[DependencyProbe]:
    """Build one probe per dependency whose callable returns its reachability.

    The callable closes over the drawn boolean, so ``check_readiness`` sees
    exactly the assignment under test — no datastore is contacted.
    """
    return [_constant_probe(name, value) for name, value in reachability.items()]


# An assignment maps a non-empty subset of dependency names to reachable/unreachable.
_reachability_assignments = st.dictionaries(
    keys=st.sampled_from(_DEPENDENCY_NAMES),
    values=st.booleans(),
    min_size=1,
    max_size=len(_DEPENDENCY_NAMES),
)


@given(reachability=_reachability_assignments)
@settings(max_examples=200)
def test_property_26_readiness_reflects_dependency_reachability(
    reachability: dict[str, bool],
) -> None:
    """Feature: foundations, Property 26: Readiness is ready exactly when all required dependencies are reachable.

    Readiness is ready iff every required dependency is reachable, names exactly
    the unreachable dependencies when not ready, recovers to ready once all are
    reachable again, and liveness is independent of dependency reachability.
    """
    report = check_readiness(_probes_for(reachability))

    all_reachable = all(reachability.values())
    expected_unreachable = {name for name, ok in reachability.items() if not ok}

    # 1. Ready iff every required dependency is reachable (Req 18.2, 18.3).
    assert report.ready is all_reachable
    # When not ready, the report names exactly the unreachable dependencies (Req 27.1).
    assert set(report.unreachable) == expected_unreachable
    # A ready report names nothing unreachable.
    assert report.ready == (len(report.unreachable) == 0)

    # 2. Recovery: once every dependency is reachable again, a fresh check is
    #    ready again — no verdict is cached (Req 27.3).
    recovered = check_readiness(_probes_for(dict.fromkeys(reachability, True)))
    assert recovered.ready is True
    assert recovered.unreachable == ()

    # 3. Liveness is independent of dependency reachability (Req 18.1): alive for
    #    every assignment, including all-unreachable.
    assert check_liveness().alive is True
