"""Unit tests of the pure parts of the Kubernetes Load_Test (task 14.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))
import lab_load_k8s as lt  # noqa: E402


# Claims: 24.1, 24.5
def test_nearest_rank_percentile_examples() -> None:
    values = list(range(1, 21))  # 20 samples
    assert lt.nearest_rank_percentile(values, 95) == 19
    assert lt.nearest_rank_percentile(values, 50) == 10
    assert lt.nearest_rank_percentile([5.0], 95) == 5.0
    with pytest.raises(ValueError):
        lt.nearest_rank_percentile([], 95)


# Claims: 24.6
def test_evaluate_targets_names_each_miss_with_value_and_threshold() -> None:
    targets = lt.default_targets()
    ok = {
        "sandbox_ready_samples": 20, "sandbox_ready_p95_seconds": 30, "sandbox_ready_max_seconds": 120,
        "concurrent_sessions_started": 10, "concurrent_sessions_held": 10, "hold_seconds": 60,
        "terminal_p95_seconds": 0.15, "grading_max_seconds": 15, "cleanup_seconds": 60,
    }
    assert lt.evaluate_targets(ok, targets) == []
    bad = {**ok, "grading_max_seconds": 15.5, "concurrent_sessions_started": 7, "cleanup_seconds": None}
    misses = {m.target: m for m in lt.evaluate_targets(bad, targets)}
    assert set(misses) == {"grading_max_seconds", "concurrent_sessions_started", "cleanup_seconds"}
    assert misses["grading_max_seconds"].measured == 15.5 and misses["grading_max_seconds"].threshold == 15


# Claims: 24.5
def test_parsers_and_summary() -> None:
    assert lt.parse_pull_seconds('Successfully pulled image "x" in 1.5s (1.5s including waiting)') == 1.5
    assert lt.parse_pull_seconds('Container image "x" already present on machine') == 0.0
    assert lt.parse_cpu_millicores("12m") == 12 and lt.parse_mebibytes("2048Ki") == 2
    assert lt.summarize([])["p95"] is None
    assert lt.per_session_cost([{"cpu_millicores": 3, "memory_mib": None}])["cpu_millicores"] == 3


# Feature: kubernetes-deployment, Property 28: Nearest-rank percentile
# Claims: 24.1, 24.5
@settings(max_examples=200, deadline=None)
@given(
    samples=st.lists(
        st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=50,
    ),
    percentile=st.floats(min_value=0.001, max_value=100, allow_nan=False, allow_infinity=False),
    seed=st.randoms(use_true_random=False),
)
def test_nearest_rank_percentile_property(samples, percentile, seed) -> None:
    """**Validates: Requirements 24.1, 24.5**"""
    result = lt.nearest_rank_percentile(samples, percentile)

    # The result is an element of the list.
    assert result in samples

    # At least that percentage of the samples are <= the result.
    at_or_below = sum(1 for s in samples if s <= result)
    assert at_or_below * 100 >= percentile * len(samples) - 1e-6

    # Shuffling does not change the result.
    shuffled = list(samples)
    seed.shuffle(shuffled)
    assert lt.nearest_rank_percentile(shuffled, percentile) == result


# Claims: 24.4
def test_grading_latency_target_is_a_15_second_maximum_across_all_samples() -> None:
    targets = lt.default_targets()
    base = {
        "sandbox_ready_samples": 20, "sandbox_ready_p95_seconds": 30, "sandbox_ready_max_seconds": 120,
        "concurrent_sessions_started": 10, "concurrent_sessions_held": 10, "hold_seconds": 60,
        "terminal_p95_seconds": 0.15, "cleanup_seconds": 60,
    }  # fmt: skip
    assert lt.GRADING_MAX_S == 15.0
    assert lt.evaluate_targets({**base, "grading_max_seconds": 15.0}, targets) == []
    (miss,) = lt.evaluate_targets({**base, "grading_max_seconds": 15.01}, targets)
    assert miss.target == "grading_max_seconds" and miss.threshold == 15.0
    # No grading sample measured counts as a miss, not a pass.
    (absent,) = lt.evaluate_targets({**base, "grading_max_seconds": None}, targets)
    assert absent.target == "grading_max_seconds"
