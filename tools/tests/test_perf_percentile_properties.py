"""Property test for the nearest-rank percentile (hands-on-labs, task 21.4).

Feature: hands-on-labs, Property 32: Nearest-rank percentile

For any non-empty list of samples and any percentile p in (0, 100], the
nearest-rank value is a member of the list, at least ceil(p/100 * n) samples
are less than or equal to it, and it is monotone in p.

**Validates: Requirements 31.4, 31.6**
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

_PROTOCOL = Path(__file__).resolve().parents[1] / "perf" / "protocol.py"
_spec = importlib.util.spec_from_file_location("perf_protocol", _PROTOCOL)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["perf_protocol"] = _mod
_spec.loader.exec_module(_mod)
percentile_nearest_rank = _mod.percentile_nearest_rank

_samples = st.lists(
    st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
    min_size=1,
    max_size=300,
)
_p = st.integers(min_value=1, max_value=10_000).map(lambda x: x / 100)  # (0, 100]


@settings(max_examples=200)
@given(values=_samples, p=_p)
def test_property_32_member_and_rank(values, p):
    result = percentile_nearest_rank(values, p)
    assert result in values
    # Integer arithmetic on hundredths avoids float rounding at rank boundaries.
    needed = -(-round(p * 100) * len(values) // 10_000)
    assert needed == math.ceil(round(p * 100) * len(values) / 10_000)
    assert sum(1 for v in values if v <= result) >= needed


@settings(max_examples=200)
@given(values=_samples, p1=_p, p2=_p)
def test_property_32_monotone_in_p(values, p1, p2):
    lo, hi = sorted((p1, p2))
    assert percentile_nearest_rank(values, lo) <= percentile_nearest_rank(values, hi)


def test_p100_is_max_and_known_example():
    assert percentile_nearest_rank([5, 1, 3], 100) == 5
    assert percentile_nearest_rank(list(range(1, 11)), 95) == 10
    assert percentile_nearest_rank(list(range(1, 11)), 50) == 5
