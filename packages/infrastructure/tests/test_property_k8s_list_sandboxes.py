"""Property 8: Listing returns only managed pods (kubernetes-deployment task 5.7).

For any mix of managed and unmanaged pods in the fake API, ``list_sandboxes`` returns exactly the
handles of the managed pods that are still present, and an empty list when none is managed.
Managed pods are provisioned through the provider; unmanaged pods are created directly on the
fake (no labels, other labels, a session label without the managed label, a managed label with a
different value, in ``labs`` and in another namespace, including other-namespace pods that carry
the managed label).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxHandle
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, pod_name
from edu_it_infrastructure.sandbox.k8s_provider import (
    LABEL_MANAGED,
    LABEL_SESSION,
    PROVIDER_NAME,
    KubernetesSandboxProvider,
)
from hypothesis import given, settings
from hypothesis import strategies as st

NS = "labs"
OTHER_NS = "other"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)

_UNMANAGED_LABELS: list[dict[str, str]] = [
    {},
    {"app": "web"},
    {LABEL_MANAGED: "0"},
    {LABEL_MANAGED: "true"},
    {LABEL_SESSION: "s-looks-managed"},
    {"edu-it.io/managed": "1"},
]


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


def _setup() -> tuple[KubernetesSandboxProvider, FakeK8sApi]:
    clock = Test_Clock()
    api = FakeK8sApi(clock, nodes=[LAB_NODE], runtime_classes=["gvisor"])
    api.add_namespace(OTHER_NS, psa="privileged")
    k8s = K8sSettings(
        namespace=NS,
        runtime_class_name="gvisor",
        runtime_kind="gvisor",
        node_selector={"edu-it/pool": "labs"},
        tolerations=(Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        node_loss_toleration_seconds=30,
        grader_gid=1001,
    )
    provider = KubernetesSandboxProvider(
        api, k8s, clock, sleep=lambda s: clock.advance(timedelta(seconds=s))
    )
    return provider, api


def _foreign_manifest(name: str, labels: dict[str, str]) -> dict[str, Any]:
    """A pod that satisfies the restricted Pod Security level, so it is admitted in ``labs``."""

    return {
        "metadata": {"name": name, "labels": dict(labels)},
        "spec": {
            "securityContext": {
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "c",
                    "image": "registry.example/other@sha256:def",
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "runAsNonRoot": True,
                    },
                }
            ],
        },
    }


_operation = st.one_of(
    st.tuples(st.just("managed"), st.integers(0, 5)),
    st.tuples(
        st.just("unmanaged_labs"), st.integers(0, 5), st.integers(0, len(_UNMANAGED_LABELS) - 1)
    ),
    st.tuples(
        st.just("unmanaged_other"), st.integers(0, 5), st.integers(0, len(_UNMANAGED_LABELS) - 1)
    ),
    st.tuples(st.just("managed_label_other_ns"), st.integers(0, 5)),
    st.tuples(st.just("destroy"), st.integers(0, 5)),
    st.tuples(st.just("destroy_unmanaged"), st.integers(0, 5)),
)


# Claims: 1.7
@settings(max_examples=60, deadline=None)
@given(ops=st.lists(_operation, max_size=20))
def test_property_8_listing_returns_only_managed_pods(ops: list[tuple[Any, ...]]) -> None:
    provider, api = _setup()
    managed: dict[str, SandboxHandle] = {}
    foreign_labs: set[str] = set()

    for op in ops:
        kind = op[0]
        if kind == "managed":
            session = f"s{op[1]}"
            handle = provider.provision(build_sandbox_spec(_Lab(), session))
            managed[session] = handle
        elif kind == "unmanaged_labs":
            name = f"ext-{op[1]}"
            if name not in foreign_labs:
                api.create_pod(NS, _foreign_manifest(name, _UNMANAGED_LABELS[op[2]]))
                foreign_labs.add(name)
        elif kind == "unmanaged_other":
            name = f"ext-{op[1]}"
            if (OTHER_NS, name) not in api._pods:
                api.create_pod(OTHER_NS, _foreign_manifest(name, _UNMANAGED_LABELS[op[2]]))
        elif kind == "managed_label_other_ns":
            name = f"mgd-{op[1]}"
            if (OTHER_NS, name) not in api._pods:
                labels = {LABEL_MANAGED: "1", LABEL_SESSION: f"x{op[1]}"}
                api.create_pod(OTHER_NS, _foreign_manifest(name, labels))
        elif kind == "destroy":
            session = f"s{op[1]}"
            if session in managed:
                provider.destroy(managed.pop(session))
        elif kind == "destroy_unmanaged":
            name = f"ext-{op[1]}"
            if name in foreign_labs:
                api.delete_pod(NS, name)
                foreign_labs.discard(name)

    listed = provider.list_sandboxes()
    expected = {SandboxHandle(pod_name(s), s, PROVIDER_NAME) for s in managed}
    assert set(listed) == expected
    assert len(listed) == len(expected)
    if not managed:
        assert list(listed) == []
