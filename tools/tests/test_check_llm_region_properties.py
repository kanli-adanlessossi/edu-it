"""Property test for the LLM region check (tools/check_llm_region.py).

Feature: kubernetes-deployment, Property 27: LLM region check

**Validates: Requirements 10.6**
"""

# Claims: 10.6

from __future__ import annotations

from check_llm_region import EU_LLM_REGIONS, check_llm_region
from hypothesis import given, settings
from hypothesis import strategies as st

_EU_REGION = st.sampled_from(sorted(EU_LLM_REGIONS))
_NON_EU_REGION = st.text(min_size=0, max_size=20).filter(lambda r: r not in EU_LLM_REGIONS)
_REGION = st.one_of(_EU_REGION, _NON_EU_REGION)


# Claims: 10.6
@settings(max_examples=200, deadline=None)
@given(region=_REGION, dev=st.booleans())
def test_accepts_iff_eu_region_or_dev_flag(region: str, dev: bool) -> None:
    message = check_llm_region(region, dev)
    accepted = message is None
    assert accepted == (dev or region in EU_LLM_REGIONS)


# Claims: 10.6
@settings(max_examples=200, deadline=None)
@given(region=_NON_EU_REGION)
def test_rejection_names_the_region(region: str) -> None:
    message = check_llm_region(region, dev=False)
    assert message is not None
    assert repr(region) in message


# Claims: 10.6
@settings(max_examples=100, deadline=None)
@given(region=_REGION)
def test_dev_flag_always_accepts(region: str) -> None:
    assert check_llm_region(region, dev=True) is None
