"""Property 6: A failed provisioning leaves no pod (kubernetes-deployment task 5.5).

For any fake-API failure point (quota, Pod Security violation, missing RuntimeClass,
unschedulable pod, pod never Ready, injected API faults) and any starting set of other pods,
``KubernetesSandboxProvider.provision`` raises an error of the matching type and afterwards no
pod exists for that session, and the other pods are unchanged. The one allowed exception is when
cleanup itself was made to fail: then ``partial_handle`` is set and names the remaining pod.

Runs on ``FakeK8sApi``; time is the ``Test_Clock`` advanced by an injected sleeper.

**Validates: Requirements 1.5, 2.11, 2.12, 6.2, 22.3, 22.8**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    ProvisionCapacityError,
    ProvisionError,
    ProvisionTimeout,
    SandboxHandle,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_api import K8sApiError
from edu_it_infrastructure.sandbox.k8s_pod_spec import (
    K8sSettings,
    Toleration,
    build_lab_pod,
    pod_name,
)
from edu_it_infrastructure.sandbox.k8s_provider import PROVIDER_NAME, KubernetesSandboxProvider
from hypothesis import given, settings
from hypothesis import strategies as st

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)

# mode -> (expected error type, a pod object exists at failure time)
_MODES: dict[str, tuple[type[Exception], bool]] = {
    "quota": (ProvisionCapacityError, False),
    "psa_violation": (ProvisionError, False),
    "missing_runtime_class": (ProvisionError, False),
    "unschedulable": (ProvisionCapacityError, True),
    "never_ready": (ProvisionTimeout, True),
    "create_api_500": (ProvisionError, False),
    "create_connection_error": (ProviderUnavailable, False),
    "read_api_500": (ProvisionError, True),
    "api_unreachable": (ProviderUnavailable, False),
}


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
        grader_gid=1001,
    )


def _spec(session: str) -> Any:
    return build_sandbox_spec(_Lab(), session)


@settings(max_examples=60, deadline=None)
@given(
    mode=st.sampled_from(sorted(_MODES)),
    others=st.integers(min_value=0, max_value=3),
    cleanup_fails=st.booleans(),
)
def test_failed_provisioning_leaves_no_pod(mode: str, others: int, cleanup_fails: bool) -> None:
    # Claims: 1.5, 2.11, 2.12, 6.2, 22.3, 22.8
    expected, pod_created = _MODES[mode]
    # Unschedulable needs a cluster without a matching node, so no other pod can be placed.
    if mode == "unschedulable":
        others = 0
    clock = Test_Clock()
    api = FakeK8sApi(
        clock,
        nodes=[FakeNode("plain")] if mode == "unschedulable" else [LAB_NODE],
        runtime_classes=["gvisor"],
        start_delay=timedelta(hours=1) if mode == "never_ready" else timedelta(seconds=2),
    )

    def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    provider = KubernetesSandboxProvider(api, _settings(), clock, sleep=sleep)

    # Other sessions' pods exist before the failing provision (default timing, so they come up).
    other_names = sorted(pod_name(f"other{i}") for i in range(others))
    for i in range(others):
        if mode == "never_ready":
            # Create directly so the "other" pods do not each wait out the ready timeout.
            api.create_pod(NS, build_lab_pod(_spec(f"other{i}"), _settings()))
        else:
            provider.provision(_spec(f"other{i}"))
    before = api.pod_names(NS)
    assert before == other_names

    if mode == "quota":
        api.set_quota(NS, {"pods": others})
    elif mode == "psa_violation":
        api.fail_next(
            "create", K8sApiError(403, "Forbidden", 'violates PodSecurity "restricted:latest"')
        )
    elif mode == "missing_runtime_class":
        api.remove_runtime_class("gvisor")
    elif mode == "create_api_500":
        api.fail_next("create", K8sApiError(500, "InternalError", "boom"))
    elif mode == "create_connection_error":
        api.fail_next("create", ConnectionError("down"))
    elif mode == "read_api_500":
        api.fail_next("read", K8sApiError(500, "InternalError", "boom"), times=1)
    elif mode == "api_unreachable":
        api.set_unreachable(True)

    cleanup_made_to_fail = cleanup_fails and pod_created
    if cleanup_made_to_fail:
        api.fail_next("delete", ConnectionError("down"), times=50)

    session = "target"
    raised: Exception | None = None
    try:
        provider.provision(_spec(session))
    except Exception as exc:
        raised = exc

    assert raised is not None, f"{mode}: provision did not raise"
    assert isinstance(raised, expected), f"{mode}: raised {type(raised).__name__}"

    api.set_unreachable(False)
    remaining = api.pod_names(NS)
    target = pod_name(session)

    if cleanup_made_to_fail:
        assert isinstance(raised, ProvisionError)
        assert raised.partial_handle == SandboxHandle(target, session, PROVIDER_NAME)
        assert remaining == sorted([*other_names, target])
    else:
        if isinstance(raised, ProvisionError):
            assert raised.partial_handle is None
        assert target not in remaining
        assert remaining == before
