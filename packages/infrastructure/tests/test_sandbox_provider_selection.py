"""Provider selection and the production refusal (Req 2.1, 2.2)."""

from __future__ import annotations

import pytest
from edu_it_infrastructure.composition import (
    CompositionSettings,
    ProviderSelectionError,
    build_sandbox_provider,
)
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider


def _env(env: str, provider: str | None) -> CompositionSettings:
    values = {"EDU_IT_ENV": env}
    if provider is not None:
        values["SANDBOX_PROVIDER"] = provider
    return CompositionSettings.from_env(values)


def test_default_selects_fake() -> None:
    assert isinstance(build_sandbox_provider(_env("development", None)), Fake_SandboxProvider)


def test_docker_selected_outside_production() -> None:
    provider = build_sandbox_provider(_env("development", "docker"))
    assert isinstance(provider, DockerSandboxProvider)


def test_docker_in_production_refuses() -> None:
    with pytest.raises(ProviderSelectionError):
        build_sandbox_provider(_env("production", "docker"))
