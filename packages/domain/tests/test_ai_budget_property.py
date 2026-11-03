"""Property test for the budget gate.

Feature: ai-content, Property 21: The budget gate holds before every attempt.

**Validates: Requirements 10.1, 10.2, 10.3, 10.4**
"""

from __future__ import annotations

from edu_it_domain.ai.budget import BudgetKind, check_attempt
from hypothesis import given
from hypothesis import strategies as st

_cents = st.integers(min_value=0, max_value=500)
_limit = st.integers(min_value=1, max_value=500)


@given(
    daily=_cents,
    track=_cents,
    job=_cents,
    daily_limit=_limit,
    track_limit=_limit,
    cap=st.none() | _limit,
)
def test_allowed_iff_every_spend_below_its_limit(
    daily: int, track: int, job: int, daily_limit: int, track_limit: int, cap: int | None
) -> None:
    decision = check_attempt(
        daily_spent_cents=daily,
        track_spent_cents=track,
        job_spent_cents=job,
        cost_cap_cents=cap,
        daily_limit_cents=daily_limit,
        track_limit_cents=track_limit,
    )
    expected_ok = daily < daily_limit and track < track_limit and (cap is None or job < cap)
    assert decision.allowed == expected_ok
    if daily >= daily_limit:
        assert decision.reached is BudgetKind.DAILY
    elif track >= track_limit:
        assert decision.reached is BudgetKind.TRACK
    elif cap is not None and job >= cap:
        assert decision.reached is BudgetKind.JOB
    else:
        assert decision.reached is None


@given(
    costs=st.lists(st.integers(min_value=0, max_value=100), max_size=40),
    daily_limit=_limit,
    track_limit=_limit,
    cap=st.none() | _limit,
    prior_daily=_cents,
    prior_track=_cents,
)
def test_no_attempt_follows_a_reached_limit(
    costs: list[int],
    daily_limit: int,
    track_limit: int,
    cap: int | None,
    prior_daily: int,
    prior_track: int,
) -> None:
    daily, track, job = prior_daily, prior_track, 0
    made = 0
    blocked_after_first = False
    for cost in costs:
        decision = check_attempt(
            daily_spent_cents=daily,
            track_spent_cents=track,
            job_spent_cents=job,
            cost_cap_cents=cap,
            daily_limit_cents=daily_limit,
            track_limit_cents=track_limit,
        )
        if not decision.allowed:
            blocked_after_first = True
            continue
        # An attempt is only made when every spend is below its limit.
        assert not blocked_after_first
        assert daily < daily_limit and track < track_limit
        assert cap is None or job < cap
        made += 1
        daily += cost
        track += cost
        job += cost
    if prior_daily >= daily_limit or prior_track >= track_limit:
        assert made == 0
