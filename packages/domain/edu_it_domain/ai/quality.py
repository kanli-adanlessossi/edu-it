"""Item quality rules (Requirements 18.4, 19.1, 19.2).

Pure functions over observations and reports. ``fold`` updates an item's
statistics one observation at a time and gives the same result as computing
them over the whole batch. Only integer sums are stored, so the average is
exact and independent of folding order.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from edu_it_domain.config import (
    ITEM_AVG_TIME_BOUNDS_MS,
    ITEM_CORRECT_RATE_BOUNDS,
    ITEM_ELAPSED_MS_MAX,
    ITEM_STAT_MIN_SAMPLES,
    REPORT_THRESHOLD,
)

#: An item is identified by its kind (question, lesson, lab) and ``stable_id``.
ItemKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ItemObservation:
    """One answer to an item; elapsed time is optional and client reported."""

    key: ItemKey
    correct: bool
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ItemStat:
    """Aggregate statistics of one item (no learner link)."""

    samples: int = 0
    correct_count: int = 0
    time_samples: int = 0
    time_total_ms: int = 0

    @property
    def correct_rate(self) -> float | None:
        """Share of correct answers, or None with no samples."""
        return self.correct_count / self.samples if self.samples else None

    @property
    def avg_time_ms(self) -> float | None:
        """Mean elapsed time over observations that had one, else None."""
        return self.time_total_ms / self.time_samples if self.time_samples else None


def clamp_elapsed_ms(
    elapsed_ms: int | None, *, elapsed_max_ms: int = ITEM_ELAPSED_MS_MAX
) -> int | None:
    """Clamp a client-reported elapsed time to ``0..elapsed_max_ms``; ``None`` stays ``None``."""
    if elapsed_ms is None:
        return None
    return min(max(elapsed_ms, 0), elapsed_max_ms)


def fold(
    stat: ItemStat,
    obs: ItemObservation,
    *,
    elapsed_max_ms: int = ITEM_ELAPSED_MS_MAX,
) -> ItemStat:
    """Return ``stat`` updated with one observation.

    Elapsed time is clamped to ``0..elapsed_max_ms``. An observation without
    elapsed time counts toward the correct rate but not the average time.
    """
    if obs.elapsed_ms is None:
        time_samples, time_total = stat.time_samples, stat.time_total_ms
    else:
        time_samples = stat.time_samples + 1
        time_total = stat.time_total_ms + min(max(obs.elapsed_ms, 0), elapsed_max_ms)
    return ItemStat(
        samples=stat.samples + 1,
        correct_count=stat.correct_count + (1 if obs.correct else 0),
        time_samples=time_samples,
        time_total_ms=time_total,
    )


def is_anomalous(
    stat: ItemStat,
    *,
    min_samples: int = ITEM_STAT_MIN_SAMPLES,
    correct_rate_bounds: tuple[float, float] = ITEM_CORRECT_RATE_BOUNDS,
    avg_time_bounds_ms: tuple[int, int] = ITEM_AVG_TIME_BOUNDS_MS,
) -> bool:
    """True when the item has enough samples and a statistic is out of bounds.

    Bounds are inclusive: a value equal to a bound is acceptable. The average
    time is judged only once it has at least ``min_samples`` timed answers.
    """
    if stat.samples < min_samples:
        return False
    rate = stat.correct_rate
    if rate is not None and not correct_rate_bounds[0] <= rate <= correct_rate_bounds[1]:
        return True
    avg = stat.avg_time_ms
    return (
        stat.time_samples >= min_samples
        and avg is not None
        and not avg_time_bounds_ms[0] <= avg <= avg_time_bounds_ms[1]
    )


def reports_reach_threshold(
    reports: Iterable[tuple[str, str]],
    content_version: str,
    *,
    threshold: int = REPORT_THRESHOLD,
) -> bool:
    """True when enough distinct learners reported the current version.

    ``reports`` holds ``(learner_id, content_version)`` pairs. Reports of
    other versions and repeat reports by one learner do not count.
    """
    learners = {learner for learner, version in reports if version == content_version}
    return len(learners) >= threshold


__all__ = [
    "ItemKey",
    "ItemObservation",
    "ItemStat",
    "clamp_elapsed_ms",
    "fold",
    "is_anomalous",
    "reports_reach_threshold",
]
