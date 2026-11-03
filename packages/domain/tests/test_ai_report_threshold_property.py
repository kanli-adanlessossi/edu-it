"""Property 35: The report threshold counts distinct learners of the current version.

Feature: ai-content, Property 35

**Validates: Requirements 18.4**
"""

from __future__ import annotations

from edu_it_domain.ai.quality import reports_reach_threshold
from edu_it_domain.config import REPORT_THRESHOLD
from hypothesis import given
from hypothesis import strategies as st

_CURRENT = "v-current"
_learners = st.sampled_from([f"user-{i}" for i in range(8)])
_versions = st.sampled_from([_CURRENT, "v-old-1", "v-old-2"])
_reports = st.lists(st.tuples(_learners, _versions), max_size=40)


@given(reports=_reports)
def test_flagged_iff_distinct_current_version_learners_reach_threshold(
    reports: list[tuple[str, str]],
) -> None:
    """Validates: Requirements 18.4"""
    distinct = {learner for learner, version in reports if version == _CURRENT}
    assert reports_reach_threshold(reports, _CURRENT) == (len(distinct) >= REPORT_THRESHOLD)


@given(reports=_reports, extra=st.lists(st.integers(min_value=0, max_value=39), max_size=20))
def test_repeats_and_old_versions_do_not_add_to_the_count(
    reports: list[tuple[str, str]], extra: list[int]
) -> None:
    """Validates: Requirements 18.4"""
    padded = list(reports)
    # Repeat existing reports and add reports on older versions of already-seen learners.
    for index in extra:
        if reports:
            learner, version = reports[index % len(reports)]
            padded.append((learner, version))
            padded.append((learner, "v-older"))
    assert reports_reach_threshold(padded, _CURRENT) == reports_reach_threshold(reports, _CURRENT)
