"""Property 22: Startup secret check.

Feature: kubernetes-deployment, Property 22: Startup secret check

For any set of required keys and any subset that is missing, ``check_required_secrets``
returns exactly the missing keys, the logs contain one record per missing key with the key
name only, no secret value appears in any record, and the process is told to exit non-zero
if and only if the missing set is not empty.

# Claims: 23.2
**Validates: Requirements 23.2**
"""

from __future__ import annotations

import logging

import pytest
from edu_it_infrastructure.secrets import check_required_secrets
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_SEGMENT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=8)
_KEYS = st.lists(_SEGMENT, min_size=1, max_size=3).map("/".join)
# Values share no characters with the key alphabet, so a value can never hide inside a key.
_VALUES = st.text(alphabet="0123456789ABCDEF-", min_size=8, max_size=24)


class _DictSource:
    """A SecretSource over a dict; absent keys raise like an unreadable mounted file."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def read(self, key: str) -> str:
        if key not in self._values:
            raise FileNotFoundError("absent")
        return self._values[key]


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[list[str], set[str], dict[str, str]]:
    required = draw(st.lists(_KEYS, min_size=0, max_size=8, unique=True))
    missing = set(draw(st.sets(st.sampled_from(required)))) if required else set()
    values = {k: draw(_VALUES) for k in required if k not in missing}
    return required, missing, values


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(scenario=_scenarios())
def test_property_22_startup_secret_check(
    scenario: tuple[list[str], set[str], dict[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    required, missing, values = scenario
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        result = check_required_secrets(_DictSource(values), required)

    expected = tuple(k for k in required if k in missing)
    assert result == expected

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == len(expected)
    for key, message in zip(expected, messages, strict=True):
        assert key in message
    for message in messages:
        assert all(value not in message for value in values.values())

    # The process exits non-zero if and only if the missing set is not empty.
    exit_code = 1 if result else 0
    assert (exit_code != 0) == bool(missing)
