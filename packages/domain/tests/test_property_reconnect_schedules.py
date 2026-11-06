"""Property 17: reconnect schedules.

Feature: kubernetes-deployment, Property 17: Reconnect schedules

For any attempt index, ``reconnect_delays("drain")`` gives at most 10 attempts with
non-decreasing delays, none above 30 s and the first at most 2 s, and
``reconnect_delays("restart")`` gives delays of at most 5 s that sum to at most 60 s.
The schedules also match the shared vectors file read by the TypeScript client.

**Validates: Requirements 14.5, 20.1**
"""

from __future__ import annotations

import json
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from edu_it_domain.labs.k8s_rules import reconnect_delays
from hypothesis import given, settings
from hypothesis import strategies as st

_VECTORS = Path(__file__).resolve().parents[3] / "tests" / "shared" / "reconnect-vectors.json"
_vectors: dict[str, Any] = json.loads(_VECTORS.read_text(encoding="utf-8"))

_DRAIN_MAX_ATTEMPTS = 10
_DRAIN_MAX_DELAY = timedelta(seconds=30)
_DRAIN_FIRST_MAX = timedelta(seconds=2)
_RESTART_MAX_DELAY = timedelta(seconds=5)
_RESTART_TOTAL = timedelta(seconds=60)

_attempt_index = st.integers(min_value=0, max_value=1000)


# Claims: 14.5, 20.1
@settings(max_examples=100, deadline=None)
@given(index=_attempt_index)
def test_drain_schedule_is_bounded_and_non_decreasing(index: int) -> None:
    delays = reconnect_delays("drain")
    assert 0 < len(delays) <= _DRAIN_MAX_ATTEMPTS
    assert delays[0] <= _DRAIN_FIRST_MAX
    assert all(d <= _DRAIN_MAX_DELAY for d in delays)
    assert all(a <= b for a, b in pairwise(delays))
    if index < len(delays):
        assert delays[index] <= _DRAIN_MAX_DELAY


# Claims: 14.5, 20.1
@settings(max_examples=100, deadline=None)
@given(index=_attempt_index)
def test_restart_schedule_has_short_delays_and_bounded_total(index: int) -> None:
    delays = reconnect_delays("restart")
    assert delays
    assert all(d <= _RESTART_MAX_DELAY for d in delays)
    assert sum(delays, timedelta(0)) <= _RESTART_TOTAL
    if index < len(delays):
        assert delays[index] <= _RESTART_MAX_DELAY


# Claims: 14.5, 20.1
@settings(max_examples=100, deadline=None)
@given(kind=st.sampled_from(["drain", "restart"]))
def test_schedules_match_shared_vectors(kind: str) -> None:
    expected = _vectors[kind]["delays_seconds"]
    got = [d.total_seconds() for d in reconnect_delays(kind)]  # type: ignore[arg-type]
    assert got == expected
