"""Example tests for ``validate_lab_config`` (Requirements 1.8, 1.11)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.config_rules import (
    LAB_CONFIG_KEYS,
    LabConfigError,
    validate_lab_config,
)


def _valid() -> dict[str, object]:
    return {key: getattr(cfg, key) for key in LAB_CONFIG_KEYS}


def test_real_config_module_is_valid() -> None:
    validate_lab_config(cfg)
    validate_lab_config(_valid())


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("LAB_SESSION_TTL", timedelta(0)),
        ("LAB_FILE_MAX_COUNT", -1),
        ("LAB_ALLOWED_WEB_ORIGINS", ()),
        ("LAB_RECONNECT_BACKOFF", (timedelta(seconds=1), timedelta(0))),
    ],
)
def test_non_positive_value_names_exactly_that_key(key: str, bad: object) -> None:
    values = _valid()
    values[key] = bad
    with pytest.raises(LabConfigError) as exc:
        validate_lab_config(values)
    assert exc.value.keys == (key,)


def test_missing_key_is_named() -> None:
    values = _valid()
    del values["LAB_IDLE_TIMEOUT"]
    with pytest.raises(LabConfigError) as exc:
        validate_lab_config(values)
    assert exc.value.keys == ("LAB_IDLE_TIMEOUT",)


def test_budget_rule_names_three_keys() -> None:
    values = _valid()
    values["LAB_GRADING_FREEZE_BOUND"] = timedelta(seconds=13)
    values["LAB_GRADING_TEARDOWN_RESERVE"] = timedelta(seconds=2)
    with pytest.raises(LabConfigError) as exc:
        validate_lab_config(values)
    assert set(exc.value.keys) == {
        "LAB_GRADING_FREEZE_BOUND",
        "LAB_GRADING_TEARDOWN_RESERVE",
        "LAB_GRADING_RUN_DEADLINE",
    }
