"""Unit tests for the grading budget and freeze functions.

Covers Req 10.7, 11.2, 11.3, 11.8.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.labs.grading import (
    PsRow,
    effective_timeout,
    learner_all_stopped,
    remaining_budget,
)

S = timedelta(seconds=1)
T0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

# Usable_Run_Budget = 30 - 10 - 8 = 12 s.
CONFIG = {
    "LAB_GRADING_RUN_DEADLINE": 30 * S,
    "LAB_GRADING_FREEZE_BOUND": 10 * S,
    "LAB_GRADING_TEARDOWN_RESERVE": 8 * S,
}


class _Cfg:
    LAB_GRADING_RUN_DEADLINE = 30 * S
    LAB_GRADING_FREEZE_BOUND = 10 * S
    LAB_GRADING_TEARDOWN_RESERVE = 8 * S


# --- remaining_budget (Req 11.8) ---------------------------------------------


def test_no_delay_no_checks_gives_full_budget() -> None:
    assert remaining_budget(T0, T0, timedelta(0), CONFIG) == 12 * S


def test_attribute_style_config_is_accepted() -> None:
    assert remaining_budget(T0, T0, timedelta(0), _Cfg()) == 12 * S


def test_queue_delay_under_budget_is_deducted() -> None:
    assert remaining_budget(T0, T0 + 5 * S, timedelta(0), CONFIG) == 7 * S


def test_queue_delay_equal_to_budget_leaves_zero() -> None:
    assert remaining_budget(T0, T0 + 12 * S, timedelta(0), CONFIG) == timedelta(0)


def test_queue_delay_over_budget_is_negative() -> None:
    assert remaining_budget(T0, T0 + 15 * S, timedelta(0), CONFIG) == -3 * S


def test_delay_is_measured_from_accepted_at_not_job_start() -> None:
    # Same job start; an earlier acceptance means a larger delay and less budget.
    start = T0 + 20 * S
    assert remaining_budget(start - 4 * S, start, timedelta(0), CONFIG) == 8 * S
    assert remaining_budget(start - 13 * S, start, timedelta(0), CONFIG) == -1 * S


def test_job_started_before_accepted_counts_as_no_delay() -> None:
    assert remaining_budget(T0, T0 - 5 * S, timedelta(0), CONFIG) == 12 * S


def test_time_spent_on_checks_is_deducted() -> None:
    assert remaining_budget(T0, T0, 4 * S, CONFIG) == 8 * S


def test_delay_and_spent_combine() -> None:
    assert remaining_budget(T0, T0 + 5 * S, 7 * S, CONFIG) == timedelta(0)
    assert remaining_budget(T0, T0 + 5 * S, 8 * S, CONFIG) == -1 * S


# --- effective_timeout (Req 11.2, 11.3) --------------------------------------


@pytest.mark.parametrize(
    ("check_timeout", "remaining", "expected"),
    [
        (5 * S, 12 * S, 5 * S),
        (12 * S, 5 * S, 5 * S),
        (5 * S, 5 * S, 5 * S),
    ],
)
def test_effective_timeout_is_min(
    check_timeout: timedelta, remaining: timedelta, expected: timedelta
) -> None:
    assert effective_timeout(check_timeout, remaining) == expected


def test_effective_timeout_zero_remaining_is_non_positive() -> None:
    assert effective_timeout(5 * S, timedelta(0)) <= timedelta(0)


def test_effective_timeout_negative_remaining_is_non_positive() -> None:
    result = effective_timeout(5 * S, -3 * S)
    assert result == -3 * S
    assert result <= timedelta(0)


def test_effective_timeout_from_exhausted_budget_means_do_not_run() -> None:
    remaining = remaining_budget(T0, T0 + 15 * S, timedelta(0), CONFIG)
    assert effective_timeout(5 * S, remaining) <= timedelta(0)


# --- learner_all_stopped (Req 10.7) ------------------------------------------


def test_running_learner_process_is_not_stopped() -> None:
    assert learner_all_stopped([PsRow("learner", "R")]) is False


def test_sleeping_learner_process_is_not_stopped() -> None:
    assert learner_all_stopped([PsRow("learner", "S")]) is False


def test_stopped_learner_processes_are_stopped() -> None:
    assert learner_all_stopped([PsRow("learner", "T"), PsRow("learner", "t")]) is True


def test_zombie_learner_process_counts_as_stopped() -> None:
    assert learner_all_stopped([PsRow("learner", "Z")]) is True


def test_one_running_among_stopped_fails() -> None:
    rows = [PsRow("learner", "T"), PsRow("learner", "Z"), PsRow("learner", "R")]
    assert learner_all_stopped(rows) is False


def test_other_user_running_process_is_ignored() -> None:
    rows = [PsRow("root", "R"), PsRow("learner", "T")]
    assert learner_all_stopped(rows) is True


def test_empty_listing_is_stopped() -> None:
    assert learner_all_stopped([]) is True


def test_unrecognized_or_empty_state_fails_closed() -> None:
    assert learner_all_stopped([PsRow("learner", "")]) is False
    assert learner_all_stopped([PsRow("learner", "?")]) is False


def test_state_with_modifiers_uses_first_letter() -> None:
    assert learner_all_stopped([PsRow("learner", "Ts")]) is True
    assert learner_all_stopped([PsRow("learner", "Sl")]) is False


def test_custom_learner_user() -> None:
    rows = [PsRow("alice", "R"), PsRow("learner", "T")]
    assert learner_all_stopped(rows, learner_user="learner") is True
    assert learner_all_stopped(rows, learner_user="alice") is False
