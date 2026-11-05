"""Property 9: Destroy is bounded and idempotent (kubernetes-deployment task 5.8).

For any fake-API delete delay (below or above ``LAB_DESTROY_TIMEOUT``), any number of repeated
destroys and already-absent pods, ``KubernetesSandboxProvider.destroy`` returns only when the pod
is absent, or raises ``SandboxError("destroy timeout")`` after at most ``LAB_DESTROY_TIMEOUT``
(plus one poll interval) of clock time with the pod labelled ``edu-it.io/destroy=pending``.
Destroying an absent pod succeeds, and repeated destroys never error once the pod is absent.

**Validates: Requirements 6.3, 6.4, 6.5**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxError, SandboxHandle
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, pod_name
from edu_it_infrastructure.sandbox.k8s_provider import (
    LABEL_DESTROY,
    READY_POLL_INTERVAL,
    KubernetesSandboxProvider,
)
from hypothesis import given, settings
from hypothesis import strategies as st

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)
_TIMEOUT_S = cfg.LAB_DESTROY_TIMEOUT.total_seconds()


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


def _setup(
    delete_delay: timedelta,
) -> tuple[KubernetesSandboxProvider, FakeK8sApi, Test_Clock]:
    clock = Test_Clock()
    api = FakeK8sApi(clock, nodes=[LAB_NODE], runtime_classes=["gvisor"], delete_delay=delete_delay)

    def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    return KubernetesSandboxProvider(api, _settings(), clock, sleep=sleep), api, clock


# Delays straddling the timeout: well below, just below, just above, far above.
_delays = st.one_of(
    st.floats(min_value=0.0, max_value=_TIMEOUT_S * 0.9),
    st.floats(min_value=_TIMEOUT_S * 1.1, max_value=_TIMEOUT_S * 100),
).map(lambda s: timedelta(seconds=s))


# Claims: 6.3, 6.4, 6.5
@settings(max_examples=60, deadline=None)
@given(delay=_delays, repeats=st.integers(min_value=1, max_value=4), session=st.just("s1"))
def test_destroy_is_bounded_and_idempotent(delay: timedelta, repeats: int, session: str) -> None:
    provider, api, clock = _setup(delay)
    spec: Any = build_sandbox_spec(_Lab(), session)
    handle = provider.provision(spec)

    start = clock.now()
    if delay < cfg.LAB_DESTROY_TIMEOUT:
        provider.destroy(handle)
        assert clock.now() - start <= cfg.LAB_DESTROY_TIMEOUT + READY_POLL_INTERVAL
        assert api.pod_names(NS) == []
    else:
        with pytest.raises(SandboxError, match="destroy timeout"):
            provider.destroy(handle)
        assert clock.now() - start <= cfg.LAB_DESTROY_TIMEOUT + READY_POLL_INTERVAL
        pending = api.list_pods(NS, f"{LABEL_DESTROY}=pending")
        assert [p.name for p in pending] == [handle.sandbox_id]
        # Once the deletion finally completes, destroy succeeds and stays successful.
        clock.advance(delay)
        assert api.pod_names(NS) == []
    for _ in range(repeats):
        provider.destroy(handle)
        assert api.pod_names(NS) == []


# Claims: 6.4
@settings(max_examples=30, deadline=None)
@given(delay=_delays, repeats=st.integers(min_value=1, max_value=4))
def test_destroying_an_absent_pod_succeeds_without_waiting(delay: timedelta, repeats: int) -> None:
    provider, api, clock = _setup(delay)
    handle = SandboxHandle(pod_name("never-created"), "never-created", "kubernetes")
    start = clock.now()
    for _ in range(repeats):
        provider.destroy(handle)
    assert clock.now() == start
    assert api.pod_names(NS) == []
