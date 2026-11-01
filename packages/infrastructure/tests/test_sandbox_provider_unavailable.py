"""Provider-unavailable behavior leaves state unchanged.

**Validates: Requirements 1.9, 1.10**
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import SANDBOX_OPERATION_TIMEOUT
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import ProviderUnavailable
from edu_it_infrastructure.fakes import Fake_SandboxProvider, ProvisionMode, Test_Clock
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider


class _Lab:
    image = "linux-base"


def test_docker_unreachable_daemon_is_provider_unavailable_within_timeout() -> None:
    # Nothing listens on port 1, so the daemon is unreachable.
    provider = DockerSandboxProvider(
        operation_timeout=SANDBOX_OPERATION_TIMEOUT, base_url="tcp://127.0.0.1:1"
    )
    spec = build_sandbox_spec(_Lab(), "s1")

    started = time.monotonic()
    with pytest.raises(ProviderUnavailable):
        provider.provision(spec)
    elapsed = time.monotonic() - started

    assert elapsed <= SANDBOX_OPERATION_TIMEOUT.total_seconds()
    # State unchanged: no client cached, no sandbox tracked; a retry fails the same way.
    assert provider._client is None
    assert provider._main_pids == {}
    with pytest.raises(ProviderUnavailable):
        provider.provision(spec)
    assert provider._client is None


def test_fake_provider_unavailable_leaves_state_unchanged() -> None:
    clock = Test_Clock(start=datetime(2024, 1, 1, tzinfo=UTC))
    provider = Fake_SandboxProvider(clock)
    spec = build_sandbox_spec(_Lab(), "s1")
    existing = provider.provision(spec)
    before = tuple(provider.list_sandboxes())
    sandboxes_before = dict(provider._sandboxes)

    provider.script_provision(ProvisionMode.UNAVAILABLE)
    with pytest.raises(ProviderUnavailable):
        provider.provision(build_sandbox_spec(_Lab(), "s2"))

    assert tuple(provider.list_sandboxes()) == before == (existing,)
    assert provider._sandboxes == sandboxes_before
    assert provider.health(existing).healthy
    # Not sticky: the next scripted/default provision succeeds.
    assert provider.provision(build_sandbox_spec(_Lab(), "s3")) != existing


def test_fake_provider_unavailable_with_empty_state_creates_nothing() -> None:
    provider = Fake_SandboxProvider(
        Test_Clock(start=datetime(2024, 1, 1, tzinfo=UTC)),
        default_mode=ProvisionMode.UNAVAILABLE,
        provision_duration=timedelta(seconds=1),
    )
    with pytest.raises(ProviderUnavailable):
        provider.provision(build_sandbox_spec(_Lab(), "s1"))
    assert provider._sandboxes == {}
    assert provider.list_sandboxes() == ()
