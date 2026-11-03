"""Property test for item statistics.

Feature: ai-content, Property 36: Statistics fold equals the batch result and
anomalies follow the bounds.

**Validates: Requirements 19.1, 19.2**
"""

from __future__ import annotations

from functools import reduce
from itertools import pairwise

from edu_it_domain.ai.quality import ItemObservation, ItemStat, fold, is_anomalous
from edu_it_domain.config import (
    ITEM_AVG_TIME_BOUNDS_MS,
    ITEM_CORRECT_RATE_BOUNDS,
    ITEM_ELAPSED_MS_MAX,
    ITEM_STAT_MIN_SAMPLES,
)
from hypothesis import given
from hypothesis import strategies as st

_KEY = ("question", "q-1")

_observations = st.builds(
    ItemObservation,
    key=st.just(_KEY),
    correct=st.booleans(),
    elapsed_ms=st.none() | st.integers(min_value=-1000, max_value=ITEM_ELAPSED_MS_MAX * 2),
)


@st.composite
def _stats(draw: st.DrawFn) -> ItemStat:
    samples = draw(st.integers(0, ITEM_STAT_MIN_SAMPLES * 3))
    time_samples = draw(st.integers(0, samples))
    return ItemStat(
        samples=samples,
        correct_count=draw(st.integers(0, samples)),
        time_samples=time_samples,
        time_total_ms=draw(st.integers(0, time_samples * ITEM_ELAPSED_MS_MAX)),
    )


@given(
    observations=st.lists(_observations, max_size=60),
    cuts=st.lists(st.integers(min_value=0, max_value=60), max_size=5),
)
def test_chunked_fold_equals_batch(observations: list[ItemObservation], cuts: list[int]) -> None:
    """Validates: Requirements 19.1, 19.2"""
    batch = reduce(fold, observations, ItemStat())

    bounds = sorted({min(c, len(observations)) for c in cuts} | {0, len(observations)})
    chunked = ItemStat()
    for start, end in pairwise(bounds):
        chunked = reduce(fold, observations[start:end], chunked)

    assert chunked == batch
    assert batch.samples == len(observations)
    assert batch.correct_count == sum(o.correct for o in observations)
    timed = [o.elapsed_ms for o in observations if o.elapsed_ms is not None]
    assert batch.time_samples == len(timed)
    assert batch.time_total_ms == sum(min(max(t, 0), ITEM_ELAPSED_MS_MAX) for t in timed)
    assert batch.correct_rate == chunked.correct_rate
    assert batch.avg_time_ms == chunked.avg_time_ms


@given(stat=_stats())
def test_anomalous_iff_enough_samples_and_out_of_bounds(stat: ItemStat) -> None:
    """Validates: Requirements 19.1, 19.2"""
    rate_low, rate_high = ITEM_CORRECT_RATE_BOUNDS
    time_low, time_high = ITEM_AVG_TIME_BOUNDS_MS

    rate_out = stat.samples > 0 and not (
        rate_low * stat.samples <= stat.correct_count <= rate_high * stat.samples
    )
    n = stat.time_samples
    time_out = n >= ITEM_STAT_MIN_SAMPLES and not (
        time_low * n <= stat.time_total_ms <= time_high * n
    )
    expected = stat.samples >= ITEM_STAT_MIN_SAMPLES and (rate_out or time_out)

    assert is_anomalous(stat) == expected
    if stat.samples < ITEM_STAT_MIN_SAMPLES:
        assert not is_anomalous(stat)
