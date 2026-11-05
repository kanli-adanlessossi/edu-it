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


def test_kubernetes_constants_have_specified_values() -> None:
    assert timedelta(seconds=30) == cfg.LAB_DESTROY_TIMEOUT
    assert timedelta(seconds=10) == cfg.LAB_PROVISION_CAPACITY_BACKOFF
    assert timedelta(seconds=60) == cfg.K8S_READY_CEILING
    assert timedelta(seconds=10) == cfg.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND
    assert timedelta(seconds=30) == cfg.LAB_INFRA_LOSS_MARK_BOUND
    assert timedelta(seconds=60) == cfg.LAB_EVICTED_POD_REMOVAL_BOUND
    assert [d.total_seconds() for d in cfg.TERMINAL_DRAIN_RECONNECT_DELAYS] == [
        1, 2, 4, 8, 16, 30, 30, 30, 30, 30,
    ]  # fmt: skip
    assert len(cfg.TERMINAL_DRAIN_RECONNECT_DELAYS) == cfg.TERMINAL_DRAIN_RECONNECT_MAX_ATTEMPTS
    assert max(cfg.TERMINAL_DRAIN_RECONNECT_DELAYS) <= cfg.TERMINAL_DRAIN_RECONNECT_CAP
    assert timedelta(seconds=2) == cfg.TERMINAL_DRAIN_RECONNECT_FIRST_WITHIN
    assert max(cfg.TERMINAL_RESTART_RECONNECT_DELAYS) <= cfg.TERMINAL_RESTART_RECONNECT_MAX_INTERVAL
    assert timedelta(seconds=60) == cfg.TERMINAL_RESTART_RECONNECT_TOTAL
    assert cfg.API_DRAIN_PERIOD_DEFAULT_SECONDS == 30
    assert cfg.API_DRAIN_PERIOD_BOUNDS_SECONDS == (5, 300)
    assert cfg.API_PRESTOP_DELAY_DEFAULT_SECONDS == 5


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("LAB_DESTROY_TIMEOUT", timedelta(0)),
        ("LAB_INFRA_LOSS_MARK_BOUND", timedelta(seconds=-1)),
        ("LAB_PROVISION_CAPACITY_BACKOFF", timedelta(seconds=9)),  # below min interval
        ("K8S_READY_CEILING", timedelta(seconds=29)),  # below provisioning timeout
    ],
)
def test_kubernetes_constant_violations_name_that_key(key: str, bad: object) -> None:
    values = _valid()
    values[key] = bad
    with pytest.raises(LabConfigError) as exc:
        validate_lab_config(values)
    assert exc.value.keys == (key,)


def test_kubernetes_boundary_values_are_valid() -> None:
    values = _valid()
    values["LAB_PROVISION_CAPACITY_BACKOFF"] = cfg.LAB_PROVISION_MIN_INTERVAL
    values["K8S_READY_CEILING"] = cfg.LAB_PROVISION_TIMEOUT
    validate_lab_config(values)


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
