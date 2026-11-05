"""Property test: provisioning is idempotent.

Feature: kubernetes-deployment, Property 7: Provisioning is idempotent

For any number of repeated ``provision`` calls for the same session, including calls that start
after an earlier call crashed after creating the pod (pod still Pending) or after the pod is
already Ready, exactly one pod with the deterministic name exists and every call returns the same
handle. A pod carrying a different session label is never adopted.

**Validates: Requirements 20.5**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import ProvisionError, SandboxHandle
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_pod_spec import (
    K8sSettings,
    Toleration,
    build_lab_pod,
    pod_name,
)
from edu_it_infrastructure.sandbox.k8s_provider import (
    LABEL_SESSION,
    PROVIDER_NAME,
    KubernetesSandboxProvider,
)
from hypothesis import given, settings
from hypothesis import strategies as st

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)

_session_ids = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=24)
_repeats = st.integers(min_value=1, max_value=6)


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


def _setup() -> tuple[KubernetesSandboxProvider, FakeK8sApi]:
    clock = Test_Clock()
    api = FakeK8sApi(clock, nodes=[LAB_NODE], runtime_classes=["gvisor"])
    provider = KubernetesSandboxProvider(
        api,
        _settings(),
        clock,
        sleep=lambda s: clock.advance(timedelta(seconds=s)),
    )
    return provider, api


def _spec(session: str) -> Any:
    return build_sandbox_spec(_Lab(), session)


# Claims: 20.5
@settings(max_examples=100, deadline=None)
@given(session=_session_ids, repeats=_repeats)
def test_repeated_provision_yields_same_handle_and_one_pod(session: str, repeats: int) -> None:
    provider, api = _setup()
    spec = _spec(session)
    expected = SandboxHandle(pod_name(session), session, PROVIDER_NAME)

    handles = [provider.provision(spec) for _ in range(repeats)]

    assert handles == [expected] * repeats
    assert api.pod_names(NS) == [pod_name(session)]
    assert provider.health(expected).healthy


# Claims: 20.5
@settings(max_examples=100, deadline=None)
@given(session=_session_ids, repeats=_repeats)
def test_rerun_after_crash_with_pending_pod_adopts_it(session: str, repeats: int) -> None:
    provider, api = _setup()
    spec = _spec(session)
    # An earlier call crashed right after creating the pod: it exists and is still Pending.
    api.create_pod(NS, build_lab_pod(spec, _settings()))
    assert not provider.health(SandboxHandle(pod_name(session), session, PROVIDER_NAME)).healthy
    expected = SandboxHandle(pod_name(session), session, PROVIDER_NAME)

    handles = [provider.provision(spec) for _ in range(repeats)]

    assert handles == [expected] * repeats
    assert api.pod_names(NS) == [pod_name(session)]


# Claims: 20.5
@settings(max_examples=100, deadline=None)
@given(session=_session_ids, other=_session_ids, repeats=_repeats)
def test_pod_with_different_session_label_is_never_adopted(
    session: str, other: str, repeats: int
) -> None:
    provider, api = _setup()
    spec = _spec(session)
    api.create_pod(NS, build_lab_pod(spec, _settings()))
    foreign = f"{other}-foreign"
    api.label_pod(NS, pod_name(session), {LABEL_SESSION: foreign})

    for _ in range(repeats):
        with pytest.raises(ProvisionError):
            provider.provision(spec)

    assert api.pod_names(NS) == [pod_name(session)]
    pods = api.list_pods(NS, "")
    assert [p.labels.get(LABEL_SESSION) for p in pods] == [foreign]
