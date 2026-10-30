"""Property test for lab Config validation (task 1.14)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.config_rules import (
    LAB_CONFIG_KEYS,
    LabConfigError,
    validate_lab_config,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_BUDGET_KEYS = (
    "LAB_GRADING_FREEZE_BOUND",
    "LAB_GRADING_TEARDOWN_RESERVE",
    "LAB_GRADING_RUN_DEADLINE",
)
_OTHER_KEYS = tuple(k for k in LAB_CONFIG_KEYS if k not in _BUDGET_KEYS)
_SECOND = timedelta(seconds=1)


def _bad_value(key: str, valid: object, state: str) -> object:
    """Return a zero/negative value of the same shape as the valid default."""
    negative = state == "negative"
    if key == "LAB_ALLOWED_WEB_ORIGINS":
        return ("",) if negative else ()
    if key == "LAB_RECONNECT_BACKOFF":
        return (_SECOND, -_SECOND) if negative else (_SECOND, timedelta(0))
    if isinstance(valid, timedelta):
        return -_SECOND if negative else timedelta(0)
    if isinstance(valid, float):
        return -1.0 if negative else 0.0
    return -1 if negative else 0


# Mostly-valid states so that single-key and valid-Config cases are common.
_state = st.sampled_from(("valid", "valid", "valid", "valid", "zero", "negative", "missing"))
_other_states = st.fixed_dictionaries({k: _state for k in _OTHER_KEYS})
_budget_draw = st.tuples(_state, st.integers(min_value=1, max_value=1000))
_budget_draws = st.fixed_dictionaries({k: _budget_draw for k in _BUDGET_KEYS})
_origins = st.one_of(
    st.none(),
    st.lists(st.text(min_size=1, max_size=8), min_size=1, max_size=4).map(tuple),
)


@settings(max_examples=200)
@given(states=_other_states, budget=_budget_draws, origins=_origins)
def test_property_lab_config_validation_names_offending_keys(
    states: dict[str, str],
    budget: dict[str, tuple[str, int]],
    origins: tuple[str, ...] | None,
) -> None:
    # Feature: hands-on-labs, Property 34: Lab Config validation names every offending key
    # **Validates: Requirements 1.8, 1.11**
    values: dict[str, object] = {}
    expected: set[str] = set()

    for key in _OTHER_KEYS:
        valid = getattr(cfg, key)
        if key == "LAB_ALLOWED_WEB_ORIGINS" and origins is not None and states[key] == "valid":
            valid = origins
        state = states[key]
        if state == "valid":
            values[key] = valid
        elif state == "missing":
            expected.add(key)
        else:
            values[key] = _bad_value(key, valid, state)
            expected.add(key)

    secs: dict[str, timedelta | None] = {}
    for key in _BUDGET_KEYS:
        state, n = budget[key]
        if state == "valid":
            secs[key] = timedelta(seconds=n)
        elif state == "zero":
            secs[key] = timedelta(0)
            expected.add(key)
        elif state == "negative":
            secs[key] = -timedelta(seconds=n)
            expected.add(key)
        else:
            secs[key] = None
            expected.add(key)
        if secs[key] is not None:
            values[key] = secs[key]

    freeze = secs["LAB_GRADING_FREEZE_BOUND"]
    reserve = secs["LAB_GRADING_TEARDOWN_RESERVE"]
    deadline = secs["LAB_GRADING_RUN_DEADLINE"]
    sum_rule = (
        freeze is not None
        and reserve is not None
        and deadline is not None
        and freeze + reserve >= deadline
    )
    if sum_rule:
        expected.update(_BUDGET_KEYS)

    if not expected:
        validate_lab_config(values)
        assert freeze is not None and reserve is not None and deadline is not None
        budget_value = cfg.usable_run_budget(deadline, freeze, reserve)
        assert budget_value == deadline - freeze - reserve
        assert budget_value > timedelta(0)
        return

    with pytest.raises(LabConfigError) as exc:
        validate_lab_config(values)
    assert set(exc.value.keys) == expected
    assert len(exc.value.keys) == len(expected)
