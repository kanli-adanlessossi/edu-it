"""Unit tests for the LLM region helper (tools/check_llm_region.py).

Feature: kubernetes-deployment, task 12.9 (the property test is task 12.10)
"""

from __future__ import annotations

import pytest
from check_llm_region import EU_LLM_REGIONS, check_llm_region, main


# Claims: 10.6
@pytest.mark.parametrize("region", sorted(EU_LLM_REGIONS))
def test_eu_regions_of_adr_0007_are_accepted(region: str) -> None:
    assert check_llm_region(region, dev=False) is None


# Claims: 10.6
def test_eu_list_matches_adr_0007_destination_regions() -> None:
    assert {
        "eu-central-1",
        "eu-north-1",
        "eu-south-1",
        "eu-south-2",
        "eu-west-1",
        "eu-west-3",
    } == EU_LLM_REGIONS


# Claims: 10.6
@pytest.mark.parametrize(
    "region", ["us-east-1", "ap-southeast-1", "global", "", "EU-CENTRAL-1", "eu-central-1 "]
)
def test_non_eu_region_is_rejected_and_named(region: str) -> None:
    message = check_llm_region(region, dev=False)
    assert message is not None
    assert repr(region) in message


# Claims: 10.6
@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1", ""])
def test_dev_flag_accepts_any_region(region: str) -> None:
    assert check_llm_region(region, dev=True) is None


# Claims: 10.6
def test_main_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eu-west-1"]) == 0
    assert main(["us-east-1"]) == 1
    assert "'us-east-1'" in capsys.readouterr().err
    assert main(["us-east-1", "--dev"]) == 0
