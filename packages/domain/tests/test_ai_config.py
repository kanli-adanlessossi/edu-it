"""Tests for the AI Config block and ``validate_ai_config`` (Req 1.3, 10.1, 10.2, 24.1)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.config import AI_CONFIG_KEYS, AiConfigError, validate_ai_config
from hypothesis import given
from hypothesis import strategies as st


def _valid() -> dict[str, object]:
    return {key: getattr(cfg, key) for key in AI_CONFIG_KEYS}


def test_fixed_values_and_real_config_valid() -> None:
    assert cfg.LLM_MAX_RETRIES == 2
    assert cfg.LLM_MAX_ATTEMPTS == 1 + cfg.LLM_MAX_RETRIES == 3
    assert cfg.LIVE_RUN_COST_CAP_CENTS == 2000
    validate_ai_config(cfg)
    validate_ai_config(_valid())


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("AI_RETENTION_AUDIT", timedelta(0)),
        ("AI_RETENTION_PROBLEM_REPORT", timedelta(days=-1)),
        ("AI_BUDGET_PER_TRACK_CENTS", 0),
        ("ITEM_CORRECT_RATE_BOUNDS", (0.9, 0.1)),
        ("READABILITY_BOUNDS", (20.0, 20.0)),
        ("ITEM_AVG_TIME_BOUNDS_MS", (1,)),
        ("PROBLEM_REPORT_CATEGORIES", ()),
    ],
)
def test_single_bad_value_names_exactly_that_key(key: str, bad: object) -> None:
    values = _valid()
    values[key] = bad
    with pytest.raises(AiConfigError) as exc:
        validate_ai_config(values)
    assert exc.value.keys == (key,)


def test_missing_key_is_named() -> None:
    values = _valid()
    del values["SIMILARITY_LIMIT"]
    with pytest.raises(AiConfigError) as exc:
        validate_ai_config(values)
    assert exc.value.keys == ("SIMILARITY_LIMIT",)


def test_attempts_must_equal_one_plus_retries() -> None:
    values = _valid()
    values["LLM_MAX_ATTEMPTS"] = 5
    with pytest.raises(AiConfigError) as exc:
        validate_ai_config(values)
    assert set(exc.value.keys) == {"LLM_MAX_ATTEMPTS", "LLM_MAX_RETRIES"}


def test_cap_above_daily_budget_names_both_keys() -> None:
    values = _valid()
    values["AI_BUDGET_DAILY_CENTS"] = 1999
    with pytest.raises(AiConfigError) as exc:
        validate_ai_config(values)
    assert set(exc.value.keys) == {"LIVE_RUN_COST_CAP_CENTS", "AI_BUDGET_DAILY_CENTS"}


def test_every_offending_key_is_named_together() -> None:
    values = _valid()
    values["AI_RETENTION_AUDIT"] = timedelta(0)
    values["READABILITY_BOUNDS"] = (80.0, 20.0)
    values["LIVE_RUN_COST_CAP_CENTS"] = 0
    with pytest.raises(AiConfigError) as exc:
        validate_ai_config(values)
    assert set(exc.value.keys) == {
        "AI_RETENTION_AUDIT",
        "READABILITY_BOUNDS",
        "LIVE_RUN_COST_CAP_CENTS",
    }
    assert all(k in str(exc.value) for k in exc.value.keys)


@given(
    daily=st.integers(min_value=1, max_value=10_000),
    cap=st.integers(min_value=1, max_value=10_000),
)
def test_cap_rule_property(daily: int, cap: int) -> None:
    values = _valid()
    values["AI_BUDGET_DAILY_CENTS"] = daily
    values["LIVE_RUN_COST_CAP_CENTS"] = cap
    if cap <= daily:
        validate_ai_config(values)
    else:
        with pytest.raises(AiConfigError):
            validate_ai_config(values)
