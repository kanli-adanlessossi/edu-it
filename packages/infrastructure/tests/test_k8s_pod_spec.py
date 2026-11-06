"""Unit tests for the pure Lab_Pod builder (layout B of ADR 0009)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pytest
from edu_it_domain import config as cfg
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.sandbox import SandboxError
from edu_it_infrastructure.sandbox.k8s_pod_spec import (
    AS_USER_CONTAINERS,
    K8sSettings,
    Toleration,
    build_lab_pod,
    container_for_user,
    pod_name,
)
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


def _settings(**over: object) -> K8sSettings:
    base: dict[str, object] = {
        "namespace": "labs",
        "runtime_class_name": "gvisor",
        "runtime_kind": "gvisor",
        "node_selector": {"edu-it/pool": "labs"},
        "tolerations": (Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        "node_loss_toleration_seconds": 30,
        "grader_gid": 1001,
    }
    base.update(over)
    return K8sSettings(**base)  # type: ignore[arg-type]


def _pod(session: str = "sess-1", **over: object) -> dict[str, Any]:
    return build_lab_pod(build_sandbox_spec(_Lab(), session), _settings(**over))


# Claims: 1.4, 1.6
def test_name_is_lab_plus_20_hex_of_sha256() -> None:
    expected = "lab-" + hashlib.sha256(b"sess-1").hexdigest()[:20]
    pod = _pod()
    assert pod["metadata"]["name"] == expected
    assert pod["metadata"]["namespace"] == "labs"
    assert pod["metadata"]["labels"] == {"edu-it.managed": "1", "edu-it.session": "sess-1"}


# Claims: 1.4, 1.6
@hsettings(max_examples=100, deadline=None)
@given(a=st.text(min_size=1), b=st.text(min_size=1))
def test_name_equal_iff_ids_equal(a: str, b: str) -> None:
    assert (pod_name(a) == pod_name(b)) == (a == b)


# Claims: 1.4 (carry-over: the same catalog image convention as the Docker provider)
@pytest.mark.parametrize(
    ("catalog", "tag", "expected"),
    [
        ("linux-base", "dev", "edu-it/lab-linux-base:dev"),
        ("python", "ci-42", "edu-it/lab-python:ci-42"),
        ("registry.example/lab@sha256:abc", "dev", "registry.example/lab@sha256:abc"),
        ("edu-it/lab-linux-base:dev", "other", "edu-it/lab-linux-base:dev"),
    ],
)
def test_catalog_image_names_resolve_like_the_docker_provider(
    catalog: str, tag: str, expected: str
) -> None:
    """Content names ``linux-base``; unresolved, Kubernetes pulls docker.io/library/linux-base."""

    pod = build_lab_pod(build_sandbox_spec(_Lab(catalog), "s"), _settings(lab_image_tag=tag))
    images = {c["image"] for c in [*pod["spec"]["containers"], *pod["spec"]["initContainers"]]}
    assert images == {expected}


# Claims: 2.2, 2.3, 2.4
def test_every_container_is_hardened_with_distinct_uids() -> None:
    containers = _pod()["spec"]["containers"]
    assert [c["name"] for c in containers] == ["learner", "grader", "owner"]
    assert [c["securityContext"]["runAsUser"] for c in containers] == [1000, 1001, 1002]
    # learner and grader are the Lab_Image users (lab-images/linux-base), so the runtime gives
    # them the image's names and /etc/group memberships, the identities lab content relies on.
    assert [c["securityContext"]["runAsGroup"] for c in containers] == [1000, 1001, 1001]
    for c in containers:
        sc = c["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"] == {"drop": ["ALL"]}
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
        assert c["resources"]["limits"] == c["resources"]["requests"]


# Claims: 2.2, 2.6
def test_pod_level_settings_and_layout_b() -> None:
    spec = _pod()["spec"]
    assert spec["shareProcessNamespace"] is False
    assert spec["automountServiceAccountToken"] is False
    assert spec["restartPolicy"] == "Never"
    assert not (spec["hostNetwork"] or spec["hostPID"] or spec["hostIPC"])
    assert spec["securityContext"]["fsGroup"] == 1001
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert all("hostPath" not in v for v in spec["volumes"])


# Claims: 2.5
def test_active_deadline_is_the_session_ttl() -> None:
    assert _pod()["spec"]["activeDeadlineSeconds"] == int(cfg.LAB_SESSION_TTL.total_seconds())


# Claims: 2.3, 2.4, 19.1
def test_learner_limits_match_docker_and_helpers_use_config() -> None:
    spec = build_sandbox_spec(_Lab(), "s")
    pod = build_lab_pod(spec, _settings())
    learner, grader, owner = pod["spec"]["containers"]
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
    assert pod["metadata"]["annotations"] == {"edu-it.io/pid-limit": str(spec.pid_limit)}


# Claims: 2.2
def test_learner_never_mounts_grader_and_grader_sees_workspace_read_only() -> None:
    learner, grader, owner = _pod()["spec"]["containers"]
    assert all(m["mountPath"] != "/grader" for m in learner["volumeMounts"])
    ws = next(m for m in grader["volumeMounts"] if m["mountPath"] == "/workspace")
    assert ws["readOnly"] is True
    assert [m["mountPath"] for m in owner["volumeMounts"]] == ["/grader"]
    vols = {v["name"]: v["emptyDir"] for v in _pod()["spec"]["volumes"]}
    assert vols["v-grader"]["medium"] == "Memory"


# Claims: 2.7, 2.8, 19.1
def test_runtime_class_node_selector_and_tolerations() -> None:
    spec = _pod(node_loss_toleration_seconds=45)["spec"]
    assert spec["runtimeClassName"] == "gvisor"
    assert spec["nodeSelector"] == {"edu-it/pool": "labs"}
    assert spec["tolerations"][0]["key"] == "edu-it/lab"
    loss = {t["key"]: t for t in spec["tolerations"] if t.get("tolerationSeconds") is not None}
    assert set(loss) == {"node.kubernetes.io/unreachable", "node.kubernetes.io/not-ready"}
    assert all(t["tolerationSeconds"] == 45 and t["effect"] == "NoExecute" for t in loss.values())


# Claims: 2.7
def test_runc_without_name_omits_runtime_class_and_other_kinds_require_one() -> None:
    assert "runtimeClassName" not in _pod(runtime_class_name=None, runtime_kind="runc")["spec"]
    with pytest.raises(ValueError):
        _pod(runtime_class_name=None, runtime_kind="kata")


# Claims: 19.1
def test_settings_node_loss_toleration_is_replaced_not_duplicated() -> None:
    own = Toleration("node.kubernetes.io/unreachable", "Exists", None, "NoExecute", 300)
    tolerations = _pod(tolerations=(own,))["spec"]["tolerations"]
    unreachable = [t for t in tolerations if t["key"] == "node.kubernetes.io/unreachable"]
    assert [t["tolerationSeconds"] for t in unreachable] == [30]


# Claims: 2.2
def test_as_user_table_maps_to_containers() -> None:
    assert dict(AS_USER_CONTAINERS) == {"learner": "learner", "grader": "grader", "root": "owner"}
    assert container_for_user("root") == "owner"
    with pytest.raises(SandboxError):
        container_for_user("nobody")


# Claims: 2.2
def test_host_mounts_and_missing_writable_mounts_are_refused() -> None:
    from dataclasses import replace

    spec = build_sandbox_spec(_Lab(), "s")
    with pytest.raises(ValueError):
        build_lab_pod(replace(spec, host_mounts=("/etc",)), _settings())
    with pytest.raises(ValueError):
        build_lab_pod(replace(spec, tmpfs_mounts=spec.tmpfs_mounts[:2]), _settings())


# Claims: 2.2, 5.1
def test_grader_dir_is_an_owner_made_subpath_so_the_owner_can_chmod_it() -> None:
    """The emptyDir root is root-owned (EPERM for a non-root chmod); the mounted /grader is a
    directory the owner UID created in an init container, with the grader group."""
    spec = _pod()["spec"]
    (init,) = spec["initContainers"]
    assert init["securityContext"]["runAsUser"] == 1002
    assert init["securityContext"]["runAsGroup"] == 1001
    assert init["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert init["command"] == ["/bin/mkdir", "-m", "2750", "/grader/bundle"]
    _learner, grader, owner = spec["containers"]
    for container in (grader, owner):
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/grader")
        assert mount["subPath"] == "bundle"
        assert not mount.get("readOnly")
    assert owner["securityContext"]["runAsGroup"] == 1001


# Claims: 5.6, 2.4
def test_every_container_init_reaps_and_never_forks() -> None:
    """A PID 1 that forks (``sleep 5 &``) dies with 'can't fork' once a fork bomb has used the pod
    PID limit; a PID 1 that does not reap (``tail -f``) keeps the bomb's zombies, and with them the
    PID limit, for ever. Every container runs the same bash init that blocks in ``read``."""
    commands = [c["command"] for c in _pod()["spec"]["containers"]]
    assert len({tuple(c) for c in commands}) == 1
    command = commands[0]
    assert command[:2] == ["/bin/bash", "-c"]
    script = command[2]
    assert script.startswith("exec 3<>/dev/ptmx;")  # a redirection, not a process
    loop = script.split("while", 1)[1]
    assert "read -t" in loop and "sleep" not in loop and "&" not in loop
    assert "trap 'exit 0' TERM" in script
    for external in ("mkfifo", "tail", "sleep", "rm "):
        assert external not in script


# Claims: 5.6
def test_timeout_kill_script_uses_builtins_only_so_it_runs_at_the_pid_limit() -> None:
    from edu_it_infrastructure.sandbox.k8s_exec import _KILL_MARKED_SCRIPT

    for external in ("tr ", "grep", "/bin/kill", "xargs", "|"):
        assert external not in _KILL_MARKED_SCRIPT
    assert "kill -s KILL" in _KILL_MARKED_SCRIPT
