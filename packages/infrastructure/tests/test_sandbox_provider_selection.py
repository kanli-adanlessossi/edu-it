"""Provider selection and the production refusal (Req 2.1, 2.2)."""

from __future__ import annotations

import pytest
from edu_it_infrastructure.composition import (
    CompositionSettings,
    ProviderSelectionError,
    build_k8s_settings,
    build_sandbox_provider,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.sandbox.docker_provider import DockerSandboxProvider
from edu_it_infrastructure.sandbox.k8s_provider import KubernetesSandboxProvider


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


def _k8s_api() -> FakeK8sApi:
    return FakeK8sApi(Test_Clock(), runtime_classes=["kata"])


# Claims: 1.1, 1.2
def test_kubernetes_selected_builds_the_provider_from_settings() -> None:
    settings = CompositionSettings.from_env(
        {
            "EDU_IT_ENV": "production",
            "SANDBOX_PROVIDER": "kubernetes",
            "K8S_LABS_NAMESPACE": "edu-labs",
            "SANDBOX_RUNTIME_KIND": "kata",
            "SANDBOX_RUNTIME_CLASS": "kata",
            "SANDBOX_NODE_SELECTOR": "edu-it/pool=labs, zone=a",
            "SANDBOX_TOLERATIONS": "edu-it/lab=true:NoSchedule,dedicated:NoSchedule",
            "SANDBOX_NODE_LOSS_TOLERATION_SECONDS": "45",
            "SANDBOX_GRADER_GID": "20001",
        }
    )
    provider = build_sandbox_provider(settings, k8s_api=_k8s_api())
    assert isinstance(provider, KubernetesSandboxProvider)

    k8s = build_k8s_settings(settings)
    assert k8s.namespace == "edu-labs"
    assert (k8s.runtime_kind, k8s.runtime_class_name) == ("kata", "kata")
    assert dict(k8s.node_selector) == {"edu-it/pool": "labs", "zone": "a"}
    assert [(t.key, t.operator, t.value, t.effect) for t in k8s.tolerations] == [
        ("edu-it/lab", "Equal", "true", "NoSchedule"),
        ("dedicated", "Exists", None, "NoSchedule"),
    ]
    assert (k8s.node_loss_toleration_seconds, k8s.grader_gid) == (45, 20001)


# Claims: 1.2
def test_kubernetes_without_a_client_adapter_refuses_and_never_falls_back_to_the_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The production client exists now (task 7.1); simulate it not being installed.
    monkeypatch.setattr(
        "edu_it_infrastructure.composition.K8S_API_MODULE", "edu_it_infrastructure.sandbox._absent"
    )
    settings = _env("development", "kubernetes")
    with pytest.raises(ProviderSelectionError, match="Kubernetes API client"):
        build_sandbox_provider(settings)


# Claims: 1.2
def test_malformed_node_selector_is_rejected() -> None:
    with pytest.raises(ValueError, match="SANDBOX_NODE_SELECTOR"):
        CompositionSettings.from_env({"SANDBOX_NODE_SELECTOR": "no-equals-sign"})
