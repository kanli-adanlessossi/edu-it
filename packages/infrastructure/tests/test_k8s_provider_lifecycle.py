"""Unit tests for ``KubernetesSandboxProvider`` lifecycle (kubernetes-deployment task 5.2).

Runs on ``FakeK8sApi``; time is the ``Test_Clock`` advanced by an injected sleeper, so nothing
sleeps on the wall clock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    ProvisionCapacityError,
    ProvisionError,
    ProvisionTimeout,
    SandboxError,
    SandboxHandle,
    SandboxProvider,
    WarmPool,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_api import K8sApiError
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, pod_name
from edu_it_infrastructure.sandbox.k8s_provider import (
    LABEL_DESTROY,
    PROVIDER_NAME,
    KubernetesSandboxProvider,
)

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)
SECRET = "internal-host.local token=abc"


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


def _settings() -> K8sSettings:
    return K8sSettings(
        namespace=NS,
        runtime_class_name="gvisor",
        runtime_kind="gvisor",
        node_selector={"edu-it/pool": "labs"},
        tolerations=(Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        node_loss_toleration_seconds=30,
        grader_gid=10001,
    )


def _spec(session: str = "s1") -> Any:
    return build_sandbox_spec(_Lab(), session)


def _setup(
    **api_kwargs: Any,
) -> tuple[KubernetesSandboxProvider, FakeK8sApi, Test_Clock, list[float]]:
    clock = Test_Clock()
    api_kwargs.setdefault("nodes", [LAB_NODE])
    api_kwargs.setdefault("runtime_classes", ["gvisor"])
    api = FakeK8sApi(clock, **api_kwargs)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(timedelta(seconds=seconds))

    return KubernetesSandboxProvider(api, _settings(), clock, sleep=sleep), api, clock, sleeps


# Claims: 1.4, 1.5, 1.6
def test_provision_returns_handle_for_deterministic_pod_and_satisfies_port() -> None:
    provider, api, _, _ = _setup()
    handle = provider.provision(_spec("s1"))
    assert isinstance(provider, SandboxProvider)
    assert handle == SandboxHandle(pod_name("s1"), "s1", PROVIDER_NAME)
    assert api.pod_names(NS) == [pod_name("s1")]
    assert provider.health(handle).healthy


# Claims: 1.5, 1.6
def test_409_with_same_session_label_adopts_the_pod() -> None:
    provider, api, _, _ = _setup()
    first = provider.provision(_spec("s1"))
    second = provider.provision(_spec("s1"))
    assert first == second
    assert api.pod_names(NS) == [pod_name("s1")]


# Claims: 1.6
def test_409_with_other_session_label_is_provision_error_and_pod_untouched() -> None:
    provider, api, _, _ = _setup()
    provider.provision(_spec("s1"))
    api._pods[(NS, pod_name("s1"))].manifest["metadata"]["labels"]["edu-it.session"] = "other"
    with pytest.raises(ProvisionError) as info:
        provider.provision(_spec("s1"))
    assert not isinstance(info.value, ProvisionCapacityError)
    assert api.pod_names(NS) == [pod_name("s1")]


# Claims: 1.5
def test_wait_uses_min_of_provision_timeout_and_ceiling() -> None:
    clock = Test_Clock()
    api = FakeK8sApi(
        clock, nodes=[LAB_NODE], runtime_classes=["gvisor"], start_delay=timedelta(hours=1)
    )
    provider = KubernetesSandboxProvider(
        api,
        _settings(),
        clock,
        sleep=lambda s: clock.advance(timedelta(seconds=s)),
        provision_timeout=timedelta(seconds=120),
        ready_ceiling=timedelta(seconds=20),
    )
    start = clock.now()
    with pytest.raises(ProvisionTimeout):
        provider.provision(_spec())
    elapsed = clock.now() - start
    assert timedelta(seconds=20) <= elapsed < timedelta(seconds=22)
    assert api.pod_names(NS) == []


# Claims: 1.5, 6.1
def test_default_wait_is_provision_timeout_bounded_by_ceiling() -> None:
    provider, _api, clock, _ = _setup(start_delay=timedelta(hours=1))
    start = clock.now()
    with pytest.raises(ProvisionTimeout):
        provider.provision(_spec())
    elapsed = clock.now() - start
    bound = min(cfg.LAB_PROVISION_TIMEOUT, cfg.K8S_READY_CEILING)
    assert bound <= elapsed < bound + timedelta(seconds=3)


# Claims: 1.7, 6.2
def test_timeout_deletes_the_pod_and_waits_until_absent() -> None:
    provider, api, _, _ = _setup(start_delay=timedelta(hours=1), delete_delay=timedelta(seconds=3))
    with pytest.raises(ProvisionTimeout) as info:
        provider.provision(_spec())
    assert info.value.partial_handle is None
    assert api.pod_names(NS) == []


# Claims: 1.8, 6.2
def test_failure_after_creation_cleans_up_and_failed_pod_is_not_waited_on() -> None:
    provider, api, clock, _ = _setup()
    api.fail_next("read", K8sApiError(500, "InternalError", SECRET))
    start = clock.now()
    with pytest.raises(ProvisionError) as info:
        provider.provision(_spec())
    assert SECRET not in str(info.value)
    assert clock.now() - start < timedelta(seconds=5)
    assert api.pod_names(NS) == []


# Claims: 6.2, 6.3
def test_cleanup_failure_sets_partial_handle() -> None:
    provider, api, _, _ = _setup(start_delay=timedelta(hours=1))
    api.fail_next("delete", ConnectionError("down"), times=10)
    with pytest.raises(ProvisionTimeout) as info:
        provider.provision(_spec("s9"))
    assert info.value.partial_handle == SandboxHandle(pod_name("s9"), "s9", PROVIDER_NAME)
    assert api.pod_names(NS) == [pod_name("s9")]


# Claims: 22.1, 22.8
def test_quota_rejection_is_capacity_error_without_raw_text() -> None:
    provider, api, _, _ = _setup()
    api.set_quota(NS, {"pods": 0})
    with pytest.raises(ProvisionCapacityError) as info:
        provider.provision(_spec())
    assert "quota" not in str(info.value).lower()
    assert api.pod_names(NS) == []


# Claims: 22.3, 22.8
def test_unschedulable_pending_at_timeout_is_capacity_error_and_cleaned() -> None:
    provider, api, _, _ = _setup(nodes=[FakeNode("plain")])
    with pytest.raises(ProvisionCapacityError):
        provider.provision(_spec())
    assert api.pod_names(NS) == []


# Claims: 2.11
def test_pod_security_rejection_is_provision_error_not_capacity() -> None:
    provider, api, _, _ = _setup()
    api.add_namespace(NS, psa="restricted")
    api.fail_next(
        "create",
        K8sApiError(403, "Forbidden", f'violates PodSecurity "restricted:latest" {SECRET}'),
    )
    with pytest.raises(ProvisionError) as info:
        provider.provision(_spec())
    assert not isinstance(info.value, ProvisionCapacityError)
    assert SECRET not in str(info.value)


# Claims: 2.12
def test_missing_runtime_class_is_provision_error_without_fallback() -> None:
    provider, api, _, _ = _setup(runtime_classes=[])
    with pytest.raises(ProvisionError) as info:
        provider.provision(_spec())
    assert not isinstance(info.value, ProvisionCapacityError)
    assert "gvisor" not in str(info.value)
    assert api.pod_names(NS) == []


# Claims: 1.8
def test_unreachable_api_on_create_is_provider_unavailable() -> None:
    provider, api, _, _ = _setup()
    api.set_unreachable(True)
    with pytest.raises(ProviderUnavailable):
        provider.provision(_spec())


# Claims: 1.5
def test_warm_pool_is_asked_first_and_none_falls_through() -> None:
    clock = Test_Clock()
    api = FakeK8sApi(clock, nodes=[LAB_NODE], runtime_classes=["gvisor"])
    warm = SandboxHandle("warm-1", "s1", PROVIDER_NAME)

    class _Pool:
        def __init__(self, result: SandboxHandle | None) -> None:
            self.result = result
            self.calls = 0

        def acquire(self, spec: Any) -> SandboxHandle | None:
            self.calls += 1
            return self.result

    def make(pool: _Pool) -> KubernetesSandboxProvider:
        assert isinstance(pool, WarmPool)
        return KubernetesSandboxProvider(
            api,
            _settings(),
            clock,
            sleep=lambda s: clock.advance(timedelta(seconds=s)),
            warm_pool=pool,
        )

    hit = _Pool(warm)
    assert make(hit).provision(_spec("s1")) == warm
    assert api.pod_names(NS) == []
    miss = _Pool(None)
    assert make(miss).provision(_spec("s2")).sandbox_id == pod_name("s2")
    assert miss.calls == 1


# Claims: 6.3, 6.4
def test_destroy_deletes_waits_for_absence_and_is_idempotent() -> None:
    provider, api, _, _ = _setup(delete_delay=timedelta(seconds=4))
    handle = provider.provision(_spec())
    provider.destroy(handle)
    assert api.pod_names(NS) == []
    provider.destroy(handle)  # 404 is success
    provider.destroy(SandboxHandle("x", "s", "docker"))  # foreign handle ignored


# Claims: 6.4, 6.5
def test_destroy_timeout_labels_pod_pending_and_raises() -> None:
    provider, api, clock, _ = _setup(delete_delay=timedelta(hours=1))
    handle = provider.provision(_spec())
    start = clock.now()
    with pytest.raises(SandboxError, match="destroy timeout"):
        provider.destroy(handle)
    assert clock.now() - start >= cfg.LAB_DESTROY_TIMEOUT
    (pod,) = api.list_pods(NS, f"{LABEL_DESTROY}=pending")
    assert pod.name == handle.sandbox_id


# Claims: 6.5
def test_destroy_with_unreachable_api_raises_provider_unavailable() -> None:
    provider, api, _, _ = _setup()
    handle = provider.provision(_spec())
    api.set_unreachable(True)
    with pytest.raises(ProviderUnavailable):
        provider.destroy(handle)


# Claims: 6.10
def test_list_sandboxes_returns_managed_pods_only() -> None:
    provider, api, _, _ = _setup()
    a = provider.provision(_spec("a"))
    b = provider.provision(_spec("b"))
    stray = _spec("c")
    manifest_api = api
    from edu_it_infrastructure.sandbox.k8s_pod_spec import build_lab_pod

    manifest = build_lab_pod(stray, _settings())
    manifest["metadata"]["labels"] = {"app": "other"}
    manifest["metadata"]["name"] = "unrelated"
    manifest_api.create_pod(NS, manifest)
    assert set(provider.list_sandboxes()) == {a, b}


# Claims: 6.10
def test_health_reports_pending_gone_and_terminating() -> None:
    provider, api, clock, _ = _setup()
    handle = provider.provision(_spec())
    assert provider.health(handle).healthy
    api.delete_pod(NS, handle.sandbox_id)
    assert provider.health(handle).detail == "terminating"
    clock.advance(timedelta(minutes=1))
    status = provider.health(handle)
    assert not status.healthy
    assert status.detail == "gone"


# Claims: 1.8, 6.1
def test_raw_api_text_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    provider, api, _, _ = _setup()
    api.fail_next("create", K8sApiError(500, "InternalError", SECRET))
    with caplog.at_level(logging.WARNING), pytest.raises(ProvisionError) as info:
        provider.provision(_spec())
    assert SECRET not in str(info.value)
    assert SECRET in caplog.text
