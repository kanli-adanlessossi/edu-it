"""Feature: kubernetes-deployment, Property 1: Sandbox backend selection.

**Validates: Requirements 1.2, 1.3**
"""

from __future__ import annotations

import pytest
from edu_it_infrastructure.composition import (
    CompositionSettings,
    ProviderSelectionError,
    SandboxBackend,
    parse_sandbox_backend,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_SUPPORTED = ("docker", "kubernetes", "fake")


# Claims: 1.2, 1.3
@settings(max_examples=100, deadline=None)
@given(
    value=st.one_of(
        st.text(),
        st.sampled_from(_SUPPORTED).flatmap(
            lambda n: st.sampled_from([n.upper(), n.capitalize(), f" {n}", f"{n} ", f"{n}x", ""])
        ),
        st.sampled_from(_SUPPORTED),
    )
)
def test_parse_returns_backend_or_names_value_and_supported(value: str) -> None:
    if value in _SUPPORTED:
        assert parse_sandbox_backend(value) is SandboxBackend(value)
        return
    with pytest.raises(ProviderSelectionError) as info:
        parse_sandbox_backend(value)
    message = str(info.value)
    assert value in message or repr(value) in message
    for name in _SUPPORTED:
        assert name in message


# Claims: 1.2
def test_kubernetes_is_a_backend() -> None:
    assert SandboxBackend.KUBERNETES.value == "kubernetes"
    assert parse_sandbox_backend("kubernetes") is SandboxBackend.KUBERNETES


# Claims: 1.3
def test_startup_settings_refuse_an_invalid_value() -> None:
    with pytest.raises(ProviderSelectionError, match="podman"):
        CompositionSettings.from_env({"SANDBOX_PROVIDER": "podman"})
