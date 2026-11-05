"""Tests for ``FakeK8sApi`` (kubernetes-deployment task 5.1).

Pods come from the real pod builder and rejections go through the real classifier, so the fake is
checked against the same code the provider will use.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import ProviderUnavailable, SandboxGone
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.k8s_api import FakeK8sApi, FakeNode, Taint
from edu_it_infrastructure.sandbox.k8s_api import (
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    ExecOutput,
    K8sApiError,
    K8sApiPort,
)
from edu_it_infrastructure.sandbox.k8s_errors import (
    CreateRejection,
    classify_create_rejection,
    map_api_exception,
)
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, build_lab_pod
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

NS = "labs"
LAB_NODE = FakeNode(
    "lab-node", labels={"edu-it/pool": "labs"}, taints=(Taint("edu-it/lab", "true"),)
)


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


def _settings(runtime: str | None = "gvisor") -> K8sSettings:
    return K8sSettings(
        namespace=NS,
        runtime_class_name=runtime,
        runtime_kind="gvisor" if runtime else "runc",
        node_selector={"edu-it/pool": "labs"},
        tolerations=(Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        node_loss_toleration_seconds=30,
        grader_gid=10001,
    )


def _pod(session: str = "s1", runtime: str | None = "gvisor") -> dict[str, Any]:
    return build_lab_pod(build_sandbox_spec(_Lab(), session), _settings(runtime))


def _api(**kwargs: Any) -> tuple[FakeK8sApi, Test_Clock]:
    clock = Test_Clock()
    kwargs.setdefault("nodes", [LAB_NODE])
    kwargs.setdefault("runtime_classes", ["gvisor"])
    return FakeK8sApi(clock, **kwargs), clock


def _reject(api: FakeK8sApi, manifest: dict[str, Any]) -> CreateRejection:
    with pytest.raises(K8sApiError) as info:
        api.create_pod(NS, manifest)
    e = info.value
    return classify_create_rejection(e.status, e.reason, e.message)


# Claims: 1.5
def test_fake_satisfies_the_port() -> None:
    api, _ = _api()
    assert isinstance(api, K8sApiPort)


# Claims: 1.5
def test_pod_is_pending_then_running_after_start_delay() -> None:
    api, clock = _api()
    manifest = _pod()
    created = api.create_pod(NS, manifest)
    assert (created.phase, created.ready) == (PHASE_PENDING, False)
    assert created.node_name == "lab-node"
    clock.advance(api.start_delay)
    view = api.read_pod(NS, manifest["metadata"]["name"])
    assert (view.phase, view.ready) == (PHASE_RUNNING, True)
    assert view.container_names == ("learner", "grader", "owner")


# Claims: 1.5
def test_duplicate_create_is_409_already_exists() -> None:
    api, _ = _api()
    api.create_pod(NS, _pod())
    assert _reject(api, _pod()) is CreateRejection.ALREADY_EXISTS


# Claims: 2.11, 22.8
def test_quota_rejection_is_capacity_and_leaves_no_pod() -> None:
    api, _ = _api()
    api.add_namespace(NS, hard={"pods": 1})
    api.create_pod(NS, _pod("a"))
    assert _reject(api, _pod("b")) is CreateRejection.CAPACITY
    assert api.pod_names(NS) == [_pod("a")["metadata"]["name"]]


# Claims: 22.8
def test_cpu_and_memory_quota_use_quantities() -> None:
    api, _ = _api()
    api.add_namespace(NS, hard={"limits.memory": "1Ki"})
    assert _reject(api, _pod()) is CreateRejection.CAPACITY


# Claims: 22.8
def test_deleted_pod_frees_quota_once_removed() -> None:
    api, clock = _api()
    api.add_namespace(NS, hard={"pods": 1})
    name = api.create_pod(NS, _pod("a")).name
    clock.advance(api.start_delay)
    api.delete_pod(NS, name)
    assert _reject(api, _pod("b")) is CreateRejection.CAPACITY
    clock.advance(api.delete_delay)
    api.create_pod(NS, _pod("b"))


# Claims: 2.11
def test_pod_security_violation_is_classified_and_builder_pod_passes() -> None:
    api, _ = _api()
    bad = copy.deepcopy(_pod())
    bad["spec"]["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = True
    assert _reject(api, bad) is CreateRejection.POD_SECURITY_VIOLATION
    assert api.pod_names(NS) == []
    api.create_pod(NS, _pod())


# Claims: 2.11
def test_host_namespaces_violate_restricted_but_not_privileged() -> None:
    api, _ = _api()
    api.add_namespace("open", psa="privileged")
    bad = copy.deepcopy(_pod())
    bad["spec"]["hostPID"] = True
    assert _reject(api, bad) is CreateRejection.POD_SECURITY_VIOLATION
    bad["metadata"]["namespace"] = "open"
    api.create_pod("open", bad)


# Claims: 2.12
def test_missing_runtime_class_is_rejected_without_fallback() -> None:
    api, _ = _api(runtime_classes=[])
    assert _reject(api, _pod()) is CreateRejection.RUNTIME_CLASS_MISSING
    assert api.pod_names(NS) == []
    api.add_runtime_class("gvisor")
    api.create_pod(NS, _pod())


# Claims: 2.12
def test_runtime_class_is_checked_before_quota_and_security() -> None:
    api, _ = _api(runtime_classes=[])
    api.add_namespace(NS, hard={"pods": 0})
    assert _reject(api, _pod()) is CreateRejection.RUNTIME_CLASS_MISSING


# Claims: 22.8
def test_unschedulable_pod_stays_pending_until_a_node_fits() -> None:
    api, clock = _api(nodes=[FakeNode("plain")])
    created = api.create_pod(NS, _pod())
    assert created.unschedulable and created.phase == PHASE_PENDING
    assert created.reason == "Unschedulable"
    clock.advance(timedelta(minutes=5))
    assert api.read_pod(NS, created.name).phase == PHASE_PENDING
    api.add_node(LAB_NODE)
    placed = api.read_pod(NS, created.name)
    assert placed.node_name == "lab-node" and not placed.unschedulable
    clock.advance(api.start_delay)
    assert api.read_pod(NS, created.name).ready


# Claims: 22.8
def test_untolerated_taint_blocks_scheduling_and_node_capacity_is_enforced() -> None:
    api, _ = _api(nodes=[FakeNode("lab-node", labels={"edu-it/pool": "labs"}, max_pods=1)])
    first = api.create_pod(NS, _pod("a"))
    assert first.node_name == "lab-node"
    assert api.create_pod(NS, _pod("b")).unschedulable
    tainted, _ = _api(
        nodes=[FakeNode("n", labels={"edu-it/pool": "labs"}, taints=(Taint("other"),))]
    )
    assert tainted.create_pod(NS, _pod()).unschedulable


# Claims: 1.5
def test_delete_keeps_pod_visible_for_the_delete_delay() -> None:
    api, clock = _api()
    name = api.create_pod(NS, _pod()).name
    clock.advance(api.start_delay)
    api.delete_pod(NS, name)
    api.delete_pod(NS, name)  # already terminating: accepted
    view = api.read_pod(NS, name)
    assert view.deletion_timestamp is not None and not view.ready
    clock.advance(api.delete_delay)
    with pytest.raises(K8sApiError) as info:
        api.read_pod(NS, name)
    assert info.value.status == 404
    assert isinstance(map_api_exception(info.value), SandboxGone)


# Claims: 1.5
def test_unscheduled_pod_is_deleted_at_once() -> None:
    api, _ = _api(nodes=[FakeNode("plain")])
    name = api.create_pod(NS, _pod()).name
    api.delete_pod(NS, name)
    assert api.pod_names(NS) == []


# Claims: 1.5
def test_active_deadline_fails_the_pod() -> None:
    api, clock = _api()
    manifest = _pod()
    name = api.create_pod(NS, manifest).name
    clock.advance(timedelta(seconds=manifest["spec"]["activeDeadlineSeconds"]))
    view = api.read_pod(NS, name)
    assert (view.phase, view.reason) == (PHASE_FAILED, "DeadlineExceeded")


# Claims: 1.5
def test_list_filters_by_label_selector_and_namespace() -> None:
    api, _ = _api()
    api.add_namespace("other")
    a = api.create_pod(NS, _pod("a"))
    api.create_pod(NS, _pod("b"))
    key, value = next(iter(a.labels.items()))
    assert len(api.list_pods(NS)) == 2
    assert len(api.list_pods(NS, key)) == 2
    assert len(api.list_pods(NS, f"{key}={value}")) == 2
    assert api.list_pods(NS, f"{key}=nope") == []
    assert api.list_pods("other") == []


# Claims: 1.5
def test_missing_namespace_is_404() -> None:
    api, _ = _api(namespaces=[])
    with pytest.raises(K8sApiError) as info:
        api.create_pod(NS, _pod())
    assert info.value.status == 404


# Claims: 1.5
def test_exec_runs_only_in_running_known_containers_and_honours_timeout() -> None:
    api, clock = _api()
    name = api.create_pod(NS, _pod()).name
    with pytest.raises(K8sApiError) as info:
        api.exec_command(NS, name, "learner", ["true"])
    assert info.value.status == 400
    clock.advance(api.start_delay)
    with pytest.raises(K8sApiError):
        api.exec_command(NS, name, "nope", ["true"])
    api.exec_handler = lambda pod, container, cmd: ExecOutput(0, stdout=cmd[-1].encode())
    assert api.exec_command(NS, name, "learner", ["echo", "hi"]).stdout == b"hi"
    api.exec_duration = timedelta(seconds=10)
    before = clock.now()
    result = api.exec_command(NS, name, "learner", ["sleep", "10"], timeout_s=2)
    assert result.timed_out and result.exit_code == 137
    assert clock.now() - before == timedelta(seconds=2)
    assert api.exec_calls[0][2:] == ("learner", ("echo", "hi"))


# Claims: 1.5
def test_exec_on_vanished_pod_is_gone_and_faults_map_to_sandbox_errors() -> None:
    api, clock = _api()
    name = api.create_pod(NS, _pod()).name
    clock.advance(api.start_delay)
    api.vanish_pod(NS, name)
    with pytest.raises(K8sApiError) as info:
        api.exec_command(NS, name, "learner", ["true"])
    assert isinstance(map_api_exception(info.value), SandboxGone)
    api.fail_next("read", ConnectionError("down"))
    with pytest.raises(ConnectionError) as down:
        api.read_pod(NS, name)
    assert isinstance(map_api_exception(down.value), ProviderUnavailable)
    api.set_unreachable(True)
    with pytest.raises(ConnectionError):
        api.list_pods(NS)


# Claims: 22.8
def test_eviction_frees_quota_and_keeps_the_pod_listed() -> None:
    api, _ = _api()
    api.add_namespace(NS, hard={"pods": 1})
    name = api.create_pod(NS, _pod("a")).name
    api.evict_pod(NS, name)
    assert api.read_pod(NS, name).reason == "Evicted"
    api.create_pod(NS, _pod("b"))


# Claims: 1.5
def test_call_latency_advances_the_clock() -> None:
    api, clock = _api(call_latency=timedelta(milliseconds=250))
    start = clock.now()
    api.list_pods(NS)
    assert clock.now() - start == timedelta(milliseconds=250)


# Claims: 22.8
@given(
    limit=st.integers(min_value=0, max_value=4),
    ops=st.lists(st.tuples(st.booleans(), st.integers(0, 5)), max_size=25),
)
@hsettings(max_examples=100, deadline=None)
def test_property_quota_is_never_exceeded_and_rejections_leave_nothing(
    limit: int, ops: list[tuple[bool, int]]
) -> None:
    """Fake invariant: live pods never exceed the pods quota; a CAPACITY rejection adds no pod."""
    api, clock = _api(delete_delay=timedelta(0))
    api.add_namespace(NS, hard={"pods": limit})
    for create, index in ops:
        before = set(api.pod_names(NS))
        manifest = _pod(f"s{index}")
        name = manifest["metadata"]["name"]
        if create:
            try:
                api.create_pod(NS, manifest)
            except K8sApiError as e:
                assert e.status in (403, 409)
                assert set(api.pod_names(NS)) == before
        elif name in before:
            api.delete_pod(NS, name)
        clock.advance(timedelta(seconds=1))
        assert len(api.pod_names(NS)) <= limit
