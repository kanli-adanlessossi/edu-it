"""API startup refuses an invalid lab Config and creates no Sandbox.

**Validates: Requirements 1.8, 1.11**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_api.app import create_app
from edu_it_domain import config as cfg
from edu_it_domain.labs.config_rules import LAB_CONFIG_KEYS, LabConfigError
from edu_it_infrastructure.fakes import Fake_SandboxProvider, Test_Clock


def _config(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {key: getattr(cfg, key) for key in LAB_CONFIG_KEYS}
    for key, value in overrides.items():
        if value is _DROP:
            del values[key]
        else:
            values[key] = value
    return values


_DROP = object()

_CASES: list[tuple[str, dict[str, object], set[str]]] = [
    ("missing", {"LAB_SESSION_TTL": _DROP}, {"LAB_SESSION_TTL"}),
    ("zero", {"LAB_IDLE_TIMEOUT": timedelta(0)}, {"LAB_IDLE_TIMEOUT"}),
    ("negative", {"LAB_PROVISION_TIMEOUT": timedelta(seconds=-1)}, {"LAB_PROVISION_TIMEOUT"}),
    ("empty-origins", {"LAB_ALLOWED_WEB_ORIGINS": ()}, {"LAB_ALLOWED_WEB_ORIGINS"}),
    (
        "freeze-plus-reserve-equals-deadline",
        {
            "LAB_GRADING_RUN_DEADLINE": timedelta(seconds=10),
            "LAB_GRADING_FREEZE_BOUND": timedelta(seconds=6),
            "LAB_GRADING_TEARDOWN_RESERVE": timedelta(seconds=4),
        },
        {
            "LAB_GRADING_RUN_DEADLINE",
            "LAB_GRADING_FREEZE_BOUND",
            "LAB_GRADING_TEARDOWN_RESERVE",
        },
    ),
    (
        "freeze-plus-reserve-above-deadline",
        {
            "LAB_GRADING_RUN_DEADLINE": timedelta(seconds=10),
            "LAB_GRADING_FREEZE_BOUND": timedelta(seconds=9),
            "LAB_GRADING_TEARDOWN_RESERVE": timedelta(seconds=9),
        },
        {
            "LAB_GRADING_RUN_DEADLINE",
            "LAB_GRADING_FREEZE_BOUND",
            "LAB_GRADING_TEARDOWN_RESERVE",
        },
    ),
]


def test_real_config_starts() -> None:
    create_app(lab_config=_config())


@pytest.mark.parametrize(("overrides", "expected"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES])
def test_api_refuses_to_start_naming_keys_and_creates_no_sandbox(
    overrides: dict[str, object],
    expected: set[str],
) -> None:
    provider = Fake_SandboxProvider(Test_Clock(start=datetime(2024, 1, 1, tzinfo=UTC)))

    with pytest.raises(LabConfigError) as exc:
        create_app(lab_config=_config(**overrides))

    assert set(exc.value.keys) == expected
    assert len(exc.value.keys) == len(expected)
    for key in expected:
        assert key in str(exc.value)
    assert provider.calls == []
    assert provider.list_sandboxes() == ()
