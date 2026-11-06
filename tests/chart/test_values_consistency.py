"""Values_File consistency (kubernetes-deployment Req 2.5, 9.4, 9.5, 16.11)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from check_values_consistency import check_values_consistency
from edu_it_domain.config import LAB_SESSION_TTL

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "edu-it"
VALUES_FILES = ["values-dev.yaml", "values-eks.yaml", "values-aks.yaml", "values-onprem.yaml"]
TTL_S = int(LAB_SESSION_TTL.total_seconds())


def _good() -> dict:
    return {
        "labs": {"sessionTtlSeconds": TTL_S},
        "ingress": {"websocket": {"readTimeoutSeconds": TTL_S, "sendTimeoutSeconds": TTL_S + 60}},
    }


def test_consistent_values_accepted() -> None:
    assert check_values_consistency(_good(), LAB_SESSION_TTL) == []


def test_ttl_mismatch_rejected() -> None:
    v = _good()
    v["labs"]["sessionTtlSeconds"] = TTL_S - 1
    assert any("labs.sessionTtlSeconds" in e for e in check_values_consistency(v, LAB_SESSION_TTL))


@pytest.mark.parametrize("key", ["readTimeoutSeconds", "sendTimeoutSeconds"])
def test_short_websocket_timeout_rejected(key: str) -> None:
    v = _good()
    v["ingress"]["websocket"][key] = TTL_S - 1
    assert any(key in e for e in check_values_consistency(v, LAB_SESSION_TTL))


def test_missing_keys_rejected() -> None:
    assert len(check_values_consistency({}, timedelta(seconds=10))) == 3


def test_kind_test_ttl_key_rejected() -> None:
    v = _good()
    v["kindTest"] = {"sessionTtlSeconds": 30}
    assert any("kindTest" in e for e in check_values_consistency(v, LAB_SESSION_TTL))


@pytest.mark.parametrize("name", VALUES_FILES)
def test_values_files_are_consistent(name: str) -> None:
    path = CHART_DIR / name
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert check_values_consistency(values, LAB_SESSION_TTL) == []


# Feature: kubernetes-deployment, Property 33: Production TTL values match the domain constant
# Claims: 9.4, 9.5, 16.11, 2.5
_near_ttl = st.one_of(
    st.just(TTL_S),
    st.integers(min_value=TTL_S - 120, max_value=TTL_S + 120),
    st.integers(min_value=0, max_value=TTL_S * 3),
)


@settings(max_examples=100, deadline=None)
@given(
    session_ttl=_near_ttl,
    read_timeout=_near_ttl,
    send_timeout=_near_ttl,
    kind_ttl=st.one_of(st.none(), st.integers(min_value=1, max_value=TTL_S)),
)
def test_property_33_production_ttl_matches_domain_constant(
    session_ttl: int, read_timeout: int, send_timeout: int, kind_ttl: int | None
) -> None:
    values: dict = {
        "labs": {"sessionTtlSeconds": session_ttl},
        "ingress": {
            "websocket": {
                "readTimeoutSeconds": read_timeout,
                "sendTimeoutSeconds": send_timeout,
            }
        },
    }
    if kind_ttl is not None:
        values["kindTest"] = {"sessionTtlSeconds": kind_ttl}

    expected_ok = (
        session_ttl == TTL_S
        and read_timeout >= TTL_S
        and send_timeout >= TTL_S
        and kind_ttl is None
    )
    assert (check_values_consistency(values, LAB_SESSION_TTL) == []) is expected_ok
