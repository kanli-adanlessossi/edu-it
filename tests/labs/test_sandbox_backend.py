"""Backend selection of the real-sandbox lab suites (kubernetes-deployment task 5.4).

Runs everywhere, with no skip: it proves that the labs isolation, grading-isolation,
tamper-resistance and Check-abuse suites can be pointed at ``KubernetesSandboxProvider``
without editing a test body (the real-cluster run is task 6.23).
"""

from __future__ import annotations

import pytest
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi
from edu_it_infrastructure.sandbox.k8s_provider import KubernetesSandboxProvider

from tests.labs import sandbox_backend as sb

_API_TARGET = f"{__name__}:fake_k8s_api"


def fake_k8s_api() -> FakeK8sApi:
    """The ``module:callable`` the Kubernetes run reads from ``EDU_IT_LAB_SUITE_K8S_API``."""
    return FakeK8sApi(Test_Clock(), runtime_classes=["gvisor"])


# Claims: 1.9, 5.7
def test_default_backend_is_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sb.BACKEND_ENV, raising=False)
    assert sb.backend_name() == "docker"
    assert not sb.is_kubernetes()
    assert sb.skip_reason() == "no Docker daemon or lab image"


# Claims: 1.9, 5.7
def test_unknown_backend_is_an_error_not_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sb.BACKEND_ENV, "podman")
    with pytest.raises(ValueError, match=sb.BACKEND_ENV):
        sb.backend_name()


# Claims: 1.9, 5.7
def test_kubernetes_backend_builds_the_kubernetes_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sb.BACKEND_ENV, "kubernetes")
    monkeypatch.setenv(sb.K8S_API_ENV, _API_TARGET)
    assert sb.provider_ready("any-image")
    assert isinstance(sb.make_provider(), KubernetesSandboxProvider)
    assert sb.skip_reason() == "no Kubernetes cluster for the lab suites"


# Claims: 1.9, 5.7
def test_kubernetes_backend_without_a_cluster_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sb.BACKEND_ENV, "kubernetes")
    monkeypatch.delenv(sb.K8S_API_ENV, raising=False)
    assert not sb.provider_ready("any-image")


# Claims: 1.9, 5.7
def test_identity_tables_cover_both_backends() -> None:
    for table in (sb.LEARNER_UID, sb.GRADER_UID, sb.OWNER_UID):
        assert set(table) == {"docker", "kubernetes"}
    # Pod layout B (ADR 0009): three distinct non-root UIDs.
    kube = {sb.LEARNER_UID["kubernetes"], sb.GRADER_UID["kubernetes"], sb.OWNER_UID["kubernetes"]}
    assert len(kube) == 3 and 0 not in kube
