"""Property test for Kubernetes API error mapping.

Feature: kubernetes-deployment, Property 10: API errors map to sandbox errors
"""

from __future__ import annotations

from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    SandboxError,
    SandboxGone,
)
from edu_it_infrastructure.sandbox.k8s_errors import map_api_exception
from hypothesis import given, settings
from hypothesis import strategies as st

_CLOSED = ["WebSocketConnectionClosedException", "BrokenPipeError", "ConnectionResetError"]
_CONNECTION = [
    "MaxRetryError",
    "NewConnectionError",
    "ProtocolError",
    "ConnectTimeoutError",
    "ReadTimeoutError",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
    "SSLError",
]
_OTHER_NAMES = ["ApiException", "ValueError", "RuntimeError", "ApiValueError", "KeyError"]

# Upper-case/digit token that cannot occur in the fixed lower-case messages.
_raw_text = st.text(alphabet="XQZ0123456789_", min_size=8, max_size=40).map(lambda s: f"RAW{s}")
_other_status = st.one_of(
    st.none(),
    st.integers(min_value=100, max_value=599).filter(lambda s: s != 404),
)


def _make(name: str, text: str, status: int | None) -> Exception:
    exc: Exception = type(name, (Exception,), {})(text)
    if status is not None:
        exc.status = status  # type: ignore[attr-defined]
    return exc


def _check_message(result: SandboxError, raw: str) -> None:
    assert isinstance(result, SandboxError)
    assert raw not in str(result)
    assert raw not in repr(result.args)


# Claims: 1.11, 1.12
@settings(max_examples=100, deadline=None)
@given(raw=_raw_text, name=st.sampled_from(_OTHER_NAMES + _CONNECTION + _CLOSED))
def test_status_404_maps_to_sandbox_gone(raw: str, name: str) -> None:
    result = map_api_exception(_make(name, raw, 404))
    assert isinstance(result, SandboxGone)
    _check_message(result, raw)


# Claims: 1.11, 1.12
@settings(max_examples=100, deadline=None)
@given(raw=_raw_text, name=st.sampled_from(_CLOSED), status=_other_status)
def test_closed_stream_maps_to_sandbox_gone(raw: str, name: str, status: int | None) -> None:
    result = map_api_exception(_make(name, raw, status))
    assert isinstance(result, SandboxGone)
    _check_message(result, raw)


# Claims: 1.11, 1.12
@settings(max_examples=100, deadline=None)
@given(raw=_raw_text, name=st.sampled_from(_CONNECTION), status=_other_status)
def test_connection_failure_maps_to_provider_unavailable(
    raw: str, name: str, status: int | None
) -> None:
    result = map_api_exception(_make(name, raw, status))
    assert isinstance(result, ProviderUnavailable)
    assert not isinstance(result, SandboxGone)
    _check_message(result, raw)


# Claims: 1.11, 1.12
@settings(max_examples=100, deadline=None)
@given(raw=_raw_text, name=st.sampled_from(_OTHER_NAMES), status=_other_status)
def test_anything_else_maps_to_generic_sandbox_error(
    raw: str, name: str, status: int | None
) -> None:
    result = map_api_exception(_make(name, raw, status))
    assert type(result) is SandboxError
    _check_message(result, raw)


# Claims: 1.11, 1.12
@settings(max_examples=100, deadline=None)
@given(
    raw=_raw_text,
    name=st.sampled_from(_OTHER_NAMES + _CONNECTION + _CLOSED),
    status=_other_status,
)
def test_result_is_always_a_sandbox_error_without_raw_text(
    raw: str, name: str, status: int | None
) -> None:
    _check_message(map_api_exception(_make(name, raw, status)), raw)
