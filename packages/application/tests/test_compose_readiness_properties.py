"""Property-based test for aggregate Compose readiness (task 10.1).

Feature: foundations, Property 35: Compose readiness passes exactly when all three services are ready in time

Property 35 (design.md): *For any* assignment of ready-within-120 s or timeout
to the Web_App, API_Service, and Worker_Service Readiness_Endpoints, the
aggregate Compose readiness check passes if and only if all three report ready
within 120 s; any single timeout fails the whole check and the failure report
names a service that did not become ready.

This drives the framework-free aggregate decision
(:func:`edu_it_application.compose_readiness.decide_compose_readiness`) directly
— the same function the wait script hands its per-service poll outcomes to — so
the property holds for whatever ready/timeout assignment Hypothesis draws, with
no live Compose stack, no HTTP server, and no real clock. Each drawn assignment
maps each of the three services to a boolean "ready within the window".

The property asserts, across every assignment:

1. **Passes iff all three ready in time** (Req 3.4, 3.6): the check passes
   exactly when every service mapped to ``True``.
2. **Any single timeout fails the whole check** (Req 3.5): whenever at least one
   service mapped to ``False`` the check fails, regardless of how many of the
   others were ready.
3. **Failure names exactly the timed-out services** (Req 3.5): on failure the
   report lists exactly the services that were not ready in time, and names at
   least one; a passing report names none.

Validates: Requirements 3.4, 3.5, 3.6

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from edu_it_application.compose_readiness import (
    COMPOSE_READINESS_SERVICES,
    decide_compose_readiness,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# An assignment maps each of the three services to ready-in-time (True) or
# timed-out (False). ``fixed_dictionaries`` guarantees the assignment covers
# exactly the three services the aggregate rule is defined over, so the drawn
# outcome is always a full, valid input to ``decide_compose_readiness``.
_ready_assignments = st.fixed_dictionaries(
    {service: st.booleans() for service in COMPOSE_READINESS_SERVICES}
)


@given(ready_in_time=_ready_assignments)
@settings(max_examples=200)
def test_property_35_compose_readiness_passes_iff_all_ready_in_time(
    ready_in_time: dict[str, bool],
) -> None:
    """Feature: foundations, Property 35: Compose readiness passes exactly when all three services are ready in time.

    The aggregate check passes iff all three services were ready within the
    window, any single timeout fails the whole check, and a failure names
    exactly the timed-out services.
    """
    report = decide_compose_readiness(ready_in_time)

    all_ready = all(ready_in_time.values())
    expected_timed_out = {service for service, ready in ready_in_time.items() if not ready}

    # 1. Passes iff every service was ready in time (Req 3.4, 3.6).
    assert report.passed is all_ready
    # 2. Any single timeout fails the whole check (Req 3.5).
    assert report.passed == (len(expected_timed_out) == 0)
    # 3. The report names exactly the timed-out services, in the canonical order.
    assert set(report.timed_out) == expected_timed_out
    assert list(report.timed_out) == [
        service for service in COMPOSE_READINESS_SERVICES if service in expected_timed_out
    ]
    # On failure the report names at least one timed-out service (Req 3.5); on
    # pass it names none.
    assert (len(report.timed_out) > 0) is (not report.passed)
