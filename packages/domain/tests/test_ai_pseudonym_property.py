"""Property 7: Pseudonymous handles do not expose or link identities.

Feature: ai-content, Property 7

**Validates: Requirements 3.3, 17.1**
"""

from __future__ import annotations

import re

from edu_it_domain.ai.pseudonym import HANDLE_PREFIX, handle
from hypothesis import given
from hypothesis import strategies as st

_keys = st.binary(min_size=16, max_size=64)
# Identifiers carry a non-hex prefix so they cannot appear inside the hex handle by chance.
_user_ids = st.uuids().map(lambda u: f"user-{u}")
_run_ids = st.uuids().map(lambda u: f"run_{u}")


@given(key=_keys, run_id=_run_ids, user_id=_user_ids)
def test_handle_contains_no_identifier_or_substring(key: bytes, run_id: str, user_id: str) -> None:
    value = handle(key, run_id, user_id)
    assert re.fullmatch(re.escape(HANDLE_PREFIX) + r"[0-9a-f]+", value)
    assert user_id not in value
    assert run_id not in value
    # Short hex substrings can match a 24-char hex digest by chance; 12+ chars cannot.
    size = 12
    for start in range(len(user_id) - size + 1):
        assert user_id[start : start + size] not in value


@given(key=_keys, run_id=_run_ids, user_id=_user_ids)
def test_handle_is_deterministic_for_same_pair(key: bytes, run_id: str, user_id: str) -> None:
    assert handle(key, run_id, user_id) == handle(key, run_id, user_id)


@given(key=_keys, run_a=_run_ids, run_b=_run_ids, user_id=_user_ids)
def test_handle_differs_across_runs_for_same_user(
    key: bytes, run_a: str, run_b: str, user_id: str
) -> None:
    if run_a != run_b:
        assert handle(key, run_a, user_id) != handle(key, run_b, user_id)


@given(key_a=_keys, key_b=_keys, run_id=_run_ids, user_id=_user_ids)
def test_handle_depends_on_secret_key(
    key_a: bytes, key_b: bytes, run_id: str, user_id: str
) -> None:
    if key_a != key_b:
        assert handle(key_a, run_id, user_id) != handle(key_b, run_id, user_id)
