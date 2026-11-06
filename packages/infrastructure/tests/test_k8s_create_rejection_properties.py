"""Property test for the pod-create rejection classifier."""

from __future__ import annotations

from edu_it_infrastructure.sandbox.k8s_errors import (
    CreateRejection,
    classify_create_rejection,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_statuses = st.one_of(st.none(), st.integers(min_value=100, max_value=599))
_reasons = st.one_of(
    st.none(),
    st.sampled_from(["AlreadyExists", "Forbidden", "Invalid", "Unauthorized", ""]),
    st.text(max_size=20),
)
_text = st.text(max_size=80)

_QUOTA = "exceeded quota"
_PSA = "violates PodSecurity"
_RC = "RuntimeClass"
_TRIGGERS = ("exceeded quota", "violates podsecurity", "runtimeclass")


def _case(s: str, flip: bool) -> str:
    return s.upper() if flip else s


def _neutral(s: str) -> bool:
    low = s.lower()
    return not any(t in low for t in _TRIGGERS)


_neutral_text = _text.filter(_neutral)


def _embed(core: str, prefix: str, suffix: str) -> str:
    return f"{prefix}{core}{suffix}"


# Feature: kubernetes-deployment, Property 4: Create-rejection classification
# Claims: 2.11, 2.12, 3.8, 22.1
# **Validates: Requirements 2.11, 2.12, 3.8, 22.1**
@settings(max_examples=200, deadline=None)
@given(
    status=_statuses,
    reason=_reasons,
    message=st.one_of(st.none(), _text),
    inject=st.sampled_from([None, _QUOTA, _PSA, _RC + " x not found"]),
    prefix=_neutral_text,
    suffix=_neutral_text,
)
def test_capacity_only_for_403_exceeded_quota(
    status: int | None,
    reason: str | None,
    message: str | None,
    inject: str | None,
    prefix: str,
    suffix: str,
) -> None:
    if inject is not None:
        message = _embed(inject, prefix, suffix)
    result = classify_create_rejection(status, reason, message)
    reports_quota = _QUOTA in (message or "").lower()
    assert (result is CreateRejection.CAPACITY) == (status == 403 and reports_quota)


# Feature: kubernetes-deployment, Property 4: Create-rejection classification
# Claims: 2.11, 2.12, 3.8, 22.1
# **Validates: Requirements 2.11, 2.12, 3.8, 22.1**
@settings(max_examples=200, deadline=None)
@given(
    status=_statuses,
    reason=_reasons,
    prefix=_neutral_text,
    suffix=_neutral_text,
    flip=st.booleans(),
    kind=st.sampled_from(["psa", "runtime"]),
)
def test_non_quota_messages_never_capacity_and_classify_as_own_kind(
    status: int | None,
    reason: str | None,
    prefix: str,
    suffix: str,
    flip: bool,
    kind: str,
) -> None:
    core = (
        _case(_PSA, flip)
        if kind == "psa"
        else f'{_case(_RC, flip)} "gvisor" {_case("not found", flip)}'
    )
    result = classify_create_rejection(status, reason, _embed(core, prefix, suffix))
    assert result is not CreateRejection.CAPACITY


# Feature: kubernetes-deployment, Property 4: Create-rejection classification
# Claims: 2.11, 2.12, 3.8, 22.1
# **Validates: Requirements 2.11, 2.12, 3.8, 22.1**
@settings(max_examples=200, deadline=None)
@given(
    prefix=_neutral_text,
    suffix=_neutral_text,
    reason=_reasons,
    flip=st.booleans(),
    runtime_status=st.sampled_from([403, 422]),
)
def test_each_design_table_pattern_classifies_correctly(
    prefix: str, suffix: str, reason: str | None, flip: bool, runtime_status: int
) -> None:
    quota = _embed(_case(_QUOTA, flip), prefix, suffix)
    assert classify_create_rejection(403, reason, quota) is CreateRejection.CAPACITY

    psa = _embed(_case(_PSA, flip), prefix, suffix)
    assert classify_create_rejection(403, reason, psa) is CreateRejection.POD_SECURITY_VIOLATION

    rc = _embed(f'{_case(_RC, flip)} "x" {_case("not found", flip)}', prefix, suffix)
    assert (
        classify_create_rejection(runtime_status, reason, rc)
        is CreateRejection.RUNTIME_CLASS_MISSING
    )

    assert (
        classify_create_rejection(409, "AlreadyExists", prefix + suffix)
        is CreateRejection.ALREADY_EXISTS
    )


# Feature: kubernetes-deployment, Property 4: Create-rejection classification
# Claims: 2.11, 2.12, 3.8, 22.1
# **Validates: Requirements 2.11, 2.12, 3.8, 22.1**
@settings(max_examples=200, deadline=None)
@given(
    status=_statuses,
    reason=_reasons.filter(lambda r: r != "AlreadyExists"),
    message=st.one_of(st.none(), _neutral_text),
)
def test_other_inputs_classify_as_other(
    status: int | None, reason: str | None, message: str | None
) -> None:
    assert classify_create_rejection(status, reason, message) is CreateRejection.OTHER
