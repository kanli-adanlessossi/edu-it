"""Property test of the Kubernetes Load_Test target evaluation (task 14.10).

Feature: kubernetes-deployment, Property 29: Load test target evaluation

For any measurements and thresholds, ``evaluate_targets`` names exactly the targets whose measured
value misses its threshold, with the measured value and the threshold, and the run fails if and
only if at least one target is missed.

Uses Hypothesis, the pinned Python property-based testing library (docs/adr/0001-versions.md).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))
import lab_load_k8s as lt

_finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
_measured = st.one_of(st.none(), _finite, st.just(math.nan))


@st.composite
def _targets(draw: st.DrawFn) -> list[lt.Target]:
    names = draw(
        st.lists(
            st.text("abcdefghij_", min_size=1, max_size=8), min_size=0, max_size=10, unique=True
        )
    )
    return [lt.Target(n, draw(_finite), draw(st.sampled_from(["max", "min"]))) for n in names]


@st.composite
def _targets_and_measurements(draw: st.DrawFn):
    targets = draw(_targets())
    measurements = {}
    for t in targets:
        if draw(st.booleans()):  # otherwise the measurement is absent
            measurements[t.name] = draw(_measured)
    # Measurements for unknown names must be ignored.
    measurements["unrelated-metric"] = draw(_finite)
    return targets, measurements


def _expected_miss(target: lt.Target, measured: float | None) -> bool:
    if measured is None or math.isnan(measured):
        return True
    return measured < target.limit if target.kind == "min" else measured > target.limit


# Claims: 24.6
@settings(max_examples=200, deadline=None)
@given(data=_targets_and_measurements())
def test_property_29_misses_are_exactly_the_missed_targets(data) -> None:
    targets, measurements = data
    misses = lt.evaluate_targets(measurements, targets)

    expected = [t for t in targets if _expected_miss(t, measurements.get(t.name))]
    assert [m.target for m in misses] == [t.name for t in expected]
    for miss, target in zip(misses, expected, strict=True):
        measured = measurements.get(target.name)
        assert miss.threshold == target.limit and miss.kind == target.kind
        assert (miss.measured is None and measured is None) or miss.measured is measured

    # The run fails if and only if the result is not empty.
    run_passes = not misses
    assert run_passes == all(not _expected_miss(t, measurements.get(t.name)) for t in targets)


# Claims: 24.6
@settings(max_examples=200, deadline=None)
@given(
    sessions=st.integers(1, 50),
    hold=st.floats(1, 600, allow_nan=False),
    min_samples=st.integers(1, 100),
    ready_p95=st.floats(0.1, 300, allow_nan=False),
    terminal_p95=st.floats(0.01, 5, allow_nan=False),
    values=st.lists(
        st.one_of(st.none(), st.floats(0, 1000, allow_nan=False)), min_size=9, max_size=9
    ),
)
def test_property_29_default_targets(
    sessions, hold, min_samples, ready_p95, terminal_p95, values
) -> None:
    targets = lt.default_targets(
        sessions=sessions,
        hold_s=hold,
        min_samples=min_samples,
        ready_p95_s=ready_p95,
        terminal_p95_s=terminal_p95,
    )
    measurements = {t.name: v for t, v in zip(targets, values, strict=True)}
    misses = lt.evaluate_targets(measurements, targets)

    expected = {t.name for t in targets if _expected_miss(t, measurements[t.name])}
    assert {m.target for m in misses} == expected
    assert len(misses) == len(expected)  # no duplicates
    # Meeting every threshold exactly (boundary values) yields no miss: the run passes.
    at_limit = {t.name: t.limit for t in targets}
    assert lt.evaluate_targets(at_limit, targets) == []
    # Any removed measurement is a miss and fails the run.
    for t in targets:
        assert [m.target for m in lt.evaluate_targets({**at_limit, t.name: None}, targets)] == [
            t.name
        ]
