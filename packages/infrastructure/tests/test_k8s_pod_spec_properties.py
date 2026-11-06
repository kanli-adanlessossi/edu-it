"""Property test for the Lab_Pod hardening and placement rules (kubernetes-deployment Prop 2)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from edu_it_domain import config as cfg
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_infrastructure.sandbox.k8s_pod_spec import (
    ANNOTATION_PID_LIMIT,
    K8sSettings,
    Toleration,
    build_lab_pod,
)
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

_NODE_LOSS = ("node.kubernetes.io/unreachable", "node.kubernetes.io/not-ready")

_token = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12)


@dataclass(frozen=True)
class _Lab:
    image: str


@st.composite
def _tolerations(draw: st.DrawFn) -> Toleration:
    return Toleration(
        key=draw(st.one_of(st.none(), _token, st.sampled_from(_NODE_LOSS))),
        operator=draw(st.sampled_from(["Equal", "Exists"])),
        value=draw(st.one_of(st.none(), _token)),
        effect=draw(st.sampled_from([None, "NoSchedule", "PreferNoSchedule", "NoExecute"])),
        toleration_seconds=draw(st.one_of(st.none(), st.integers(0, 3600))),
    )


@st.composite
def _inputs(draw: st.DrawFn):  # type: ignore[no-untyped-def]
    base = build_sandbox_spec(
        _Lab(image="registry.example/lab@sha256:" + draw(_token)),
        draw(st.text(min_size=1, max_size=40)),
    )
    spec = replace(
        base,
        cpu_limit=draw(st.integers(1, 16)),
        memory_limit_bytes=draw(st.integers(64 * 2**20, 16 * 2**30)),
        pid_limit=draw(st.integers(1, 4096)),
        writable_quota_bytes=draw(st.integers(1 * 2**20, 8 * 2**30)),
        read_only_root=draw(st.booleans()),
    )
    kind = draw(st.sampled_from(["gvisor", "kata", "runc"]))
    class_name = draw(st.one_of(st.none(), _token)) if kind == "runc" else draw(_token)
    settings = K8sSettings(
        namespace=draw(_token),
        runtime_class_name=class_name,
        runtime_kind=kind,  # type: ignore[arg-type]
        node_selector=draw(st.dictionaries(_token, _token, max_size=4)),
        tolerations=tuple(draw(st.lists(_tolerations(), max_size=5))),
        node_loss_toleration_seconds=draw(st.integers(0, 3600)),
        grader_gid=draw(st.integers(1, 65535)),
    )
    return spec, settings


# Feature: kubernetes-deployment, Property 2: Pod manifest satisfies hardening and placement rules
# Claims: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
# **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**
@hsettings(max_examples=100, deadline=None)
@given(data=_inputs())
def test_pod_manifest_satisfies_hardening_and_placement_rules(data) -> None:  # type: ignore[no-untyped-def]
    spec, settings = data
    pod = build_lab_pod(spec, settings)
    pod_spec = pod["spec"]

    # Hardening on every container (2.2, 2.3, 2.4).
    containers = pod_spec["containers"]
    assert containers
    uids = [c["securityContext"]["runAsUser"] for c in containers]
    assert len(set(uids)) == len(uids) and all(uid > 0 for uid in uids)
    for c in containers:
        sc = c["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
        assert sc["readOnlyRootFilesystem"] is spec.read_only_root
        limits = c["resources"]["limits"]
        assert limits and limits == c["resources"]["requests"]

    # Learner limits mirror the spec; helpers use the configured values (2.3, 2.4).
    learner, grader, owner = containers
    assert learner["resources"]["limits"] == {
        "cpu": str(spec.cpu_limit),
        "memory": str(spec.memory_limit_bytes),
        "ephemeral-storage": str(spec.writable_quota_bytes),
    }
    assert grader["resources"]["limits"] == {
        "cpu": f"{cfg.K8S_GRADER_CPU_MILLI}m",
        "memory": str(cfg.K8S_GRADER_MEMORY_BYTES),
    }
    assert owner["resources"]["limits"] == {
        "cpu": f"{cfg.K8S_HELPER_CPU_MILLI}m",
        "memory": str(cfg.K8S_HELPER_MEMORY_BYTES),
    }

    # Pod-level flags (2.2, 2.6).
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["hostNetwork"] is False
    assert pod_spec["hostPID"] is False
    assert pod_spec["hostIPC"] is False
    assert pod_spec["shareProcessNamespace"] is False
    assert pod_spec["restartPolicy"] == "Never"
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert pod_spec["securityContext"]["fsGroup"] == settings.grader_gid
    assert all("hostPath" not in v for v in pod_spec["volumes"])

    # Session deadline (2.5) and pid-limit annotation (2.4).
    assert pod_spec["activeDeadlineSeconds"] == int(cfg.LAB_SESSION_TTL.total_seconds())
    assert pod["metadata"]["annotations"][ANNOTATION_PID_LIMIT] == str(spec.pid_limit)

    # Runtime class (2.7).
    if settings.runtime_class_name:
        assert pod_spec["runtimeClassName"] == settings.runtime_class_name
    else:
        assert "runtimeClassName" not in pod_spec

    # Node selector and tolerations (2.8).
    assert pod_spec["nodeSelector"] == dict(settings.node_selector)
    tolerations = pod_spec["tolerations"]
    for key in _NODE_LOSS:
        loss = [t for t in tolerations if t.get("key") == key and t.get("effect") == "NoExecute"]
        assert len(loss) == 1
        assert loss[0]["tolerationSeconds"] == settings.node_loss_toleration_seconds
    kept = [
        t.to_manifest()
        for t in settings.tolerations
        if not (t.key in _NODE_LOSS and t.effect in ("NoExecute", None))
    ]
    assert tolerations[: len(kept)] == kept
