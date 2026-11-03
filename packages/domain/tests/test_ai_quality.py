"""Unit tests for the item quality rules (Requirements 18.4, 19.1, 19.2)."""

from __future__ import annotations

from edu_it_domain.ai.quality import (
    ItemObservation,
    ItemStat,
    fold,
    is_anomalous,
    reports_reach_threshold,
)
from edu_it_domain.config import ITEM_ELAPSED_MS_MAX

_KEY = ("question", "q-1")


def _obs(correct: bool, elapsed_ms: int | None = None) -> ItemObservation:
    return ItemObservation(_KEY, correct, elapsed_ms)


def test_fold_counts_and_averages() -> None:
    stat = ItemStat()
    for obs in (_obs(True, 1000), _obs(False, 3000), _obs(True)):
        stat = fold(stat, obs)
    assert stat.samples == 3
    assert stat.correct_rate == 2 / 3
    assert stat.avg_time_ms == 2000


def test_fold_clamps_elapsed_time() -> None:
    stat = fold(ItemStat(), _obs(True, ITEM_ELAPSED_MS_MAX * 10))
    assert stat.avg_time_ms == ITEM_ELAPSED_MS_MAX
    assert fold(ItemStat(), _obs(True, -5)).avg_time_ms == 0


def test_empty_stat_has_no_rates() -> None:
    assert ItemStat().correct_rate is None
    assert ItemStat().avg_time_ms is None


def test_not_anomalous_below_min_samples() -> None:
    stat = ItemStat(samples=19, correct_count=0, time_samples=19, time_total_ms=0)
    assert not is_anomalous(stat)


def test_anomalous_on_correct_rate_and_time_bounds() -> None:
    assert is_anomalous(ItemStat(samples=20, correct_count=0))
    assert is_anomalous(ItemStat(samples=20, correct_count=20))
    assert is_anomalous(ItemStat(samples=20, correct_count=10, time_samples=20, time_total_ms=20))
    assert is_anomalous(
        ItemStat(samples=20, correct_count=10, time_samples=20, time_total_ms=20 * 400000)
    )


def test_bounds_are_inclusive() -> None:
    # 2/20 = 0.10 is the lower rate bound, 3000 ms the lower time bound.
    stat = ItemStat(samples=20, correct_count=2, time_samples=20, time_total_ms=20 * 3000)
    assert not is_anomalous(stat)


def test_reports_count_distinct_learners_of_current_version() -> None:
    reports = [("a", "v2"), ("a", "v2"), ("b", "v2"), ("c", "v1")]
    assert not reports_reach_threshold(reports, "v2")
    assert reports_reach_threshold([*reports, ("c", "v2")], "v2")
    assert not reports_reach_threshold([*reports, ("c", "v2")], "v3")
