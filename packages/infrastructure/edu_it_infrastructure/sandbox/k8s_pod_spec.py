"""Pure translation of a ``SandboxSpec`` into a Lab_Pod manifest dictionary.

No Kubernetes client import and no I/O, so the hardening and placement rules can be property
tested without a cluster (kubernetes-deployment Req 1.4, 1.6, 2.2-2.8, 19.1).

The layout follows ADR 0009 ("Spike result"): layout B. Three containers with distinct non-root
UIDs (``learner`` 10000, ``grader`` 10001, ``owner`` 10002) and ``shareProcessNamespace: false``.
If the recorded layout changes, this module, the ``as_user`` table and Property 2 change together.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

from edu_it_domain import config as _cfg
from edu_it_domain.labs.models import SandboxSpec, TmpfsMount
from edu_it_domain.ports.sandbox import SandboxError

__all__ = [
    "ANNOTATION_PID_LIMIT",
    "AS_USER_CONTAINERS",
    "CONTAINER_GRADER",
    "CONTAINER_LEARNER",
    "CONTAINER_OWNER",
    "POD_NAME_HEX_LENGTH",
    "POD_NAME_PREFIX",
    "K8sSettings",
    "Toleration",
    "build_lab_pod",
    "container_for_user",
    "pod_name",
]

POD_NAME_PREFIX: Final[str] = "lab-"
POD_NAME_HEX_LENGTH: Final[int] = 20

#: Annotation carrying the Docker PID limit; enforced by the node pool ``podPidsLimit`` (OQ-6).
ANNOTATION_PID_LIMIT: Final[str] = "edu-it.io/pid-limit"

CONTAINER_LEARNER: Final[str] = "learner"
CONTAINER_GRADER: Final[str] = "grader"
CONTAINER_OWNER: Final[str] = "owner"

LEARNER_UID: Final[int] = 10000
GRADER_UID: Final[int] = 10001
OWNER_UID: Final[int] = 10002

#: ``as_user`` of ``SandboxProvider.exec`` to the container that runs the command (layout B).
#: ``root`` is the "owner of the grading bundle" identity of ``GradeRun``, mapped to a non-root UID.
AS_USER_CONTAINERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "learner": CONTAINER_LEARNER,
        "grader": CONTAINER_GRADER,
        "root": CONTAINER_OWNER,
    }
)

_WORKSPACE = "/workspace"
_GRADER = "/grader"

#: Name of the init container that creates the owner-owned ``/grader`` directory.
INIT_GRADER_DIR: Final[str] = "init-grader"
#: Directory of the ``v-grader`` volume that the grader and owner containers mount as ``/grader``.
GRADER_SUBPATH: Final[str] = "bundle"
#: setgid (group inherited from the volume root) plus owner rwx, group r-x, nothing for others.
GRADER_DIR_MODE: Final[str] = "2750"

#: PID 1 of EVERY container (learner, grader, owner) runs no user code, survives stopped children,
#: reaps orphans and NEVER forks (ADR 0009). A bare ``sleep`` or ``tail -f`` entrypoint does not
#: reap: after a Check's fork bomb is killed its processes stay zombies of PID 1 and hold the
#: pod PID limit for ever, so no exec can start again. A loop that forks ``sleep`` dies with
#: "can't fork" (exit 2: the container ends, and with ``restartPolicy: Never`` the pod) once a
#: fork bomb has used the pod PID limit. This init opens a pty master (``/dev/ptmx``, so it needs
#: no writable mount and nothing writes to it), blocks in the ``read`` builtin on it with a 1 s
#: timeout, and bash reaps every orphan between reads. SIGTERM exits at once.
_REAPING_INIT: Final[tuple[str, ...]] = (
    "/bin/bash",
    "-c",
    "exec 3<>/dev/ptmx; trap 'exit 0' TERM; while :; do read -t 1 -u 3 _; done",
)

_NODE_LOSS_KEYS: Final[tuple[str, ...]] = (
    "node.kubernetes.io/unreachable",
    "node.kubernetes.io/not-ready",
)


@dataclass(frozen=True, slots=True)
class Toleration:
    """One pod toleration; ``None`` fields are omitted from the manifest."""

    key: str | None = None
    operator: Literal["Equal", "Exists"] = "Equal"
    value: str | None = None
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] | None = None
    toleration_seconds: int | None = None

    def to_manifest(self) -> dict[str, Any]:
        out: dict[str, Any] = {"operator": self.operator}
        if self.key is not None:
            out["key"] = self.key
        if self.value is not None:
            out["value"] = self.value
        if self.effect is not None:
            out["effect"] = self.effect
        if self.toleration_seconds is not None:
            out["tolerationSeconds"] = self.toleration_seconds
        return out


@dataclass(frozen=True, slots=True)
class K8sSettings:
    """Placement and runtime settings read from the Chart values."""

    namespace: str
    runtime_class_name: str | None
    runtime_kind: Literal["gvisor", "kata", "runc"]
    node_selector: Mapping[str, str]
    tolerations: tuple[Toleration, ...]
    node_loss_toleration_seconds: int
    grader_gid: int
    #: Optional PriorityClass of Lab_Pods (task 6.9); ``None`` leaves the cluster default.
    priority_class_name: str | None = None


def pod_name(session_stable_id: str) -> str:
    """Deterministic pod name: ``lab-`` plus the first 20 hex characters of the sha256."""

    digest = hashlib.sha256(str(session_stable_id).encode("utf-8")).hexdigest()
    return POD_NAME_PREFIX + digest[:POD_NAME_HEX_LENGTH]


def container_for_user(as_user: str) -> str:
    """Container that runs a command for ``as_user``; unknown values raise ``SandboxError``."""

    try:
        return AS_USER_CONTAINERS[as_user]
    except KeyError:
        raise SandboxError(
            f"unknown as_user {as_user!r}; expected one of {sorted(AS_USER_CONTAINERS)}"
        ) from None


def _security_context(uid: int, read_only_root: bool) -> dict[str, Any]:
    return {
        "runAsUser": uid,
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": read_only_root,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _owner_context(read_only_root: bool, grader_gid: int) -> dict[str, Any]:
    """Owner identity: its primary group is the grader group, so every file it creates is
    group-readable by the grader whether or not the parent directory keeps its setgid bit."""

    context = _security_context(OWNER_UID, read_only_root)
    context["runAsGroup"] = grader_gid
    return context


def _volume_name(path: str) -> str:
    return "v-" + (path.strip("/").replace("/", "-") or "root")


def _volumes(mounts: tuple[TmpfsMount, ...]) -> list[dict[str, Any]]:
    volumes: list[dict[str, Any]] = []
    for mount in mounts:
        # Every writable mount is a tmpfs of exactly ``size_bytes``, as in the Docker provider: a
        # write past the size fails at once with ENOSPC. A disk-backed emptyDir would only be
        # evicted later by the kubelet. tmpfs pages count against the container memory limit, in
        # both providers.
        empty_dir: dict[str, Any] = {"medium": "Memory", "sizeLimit": str(mount.size_bytes)}
        volumes.append({"name": _volume_name(mount.path), "emptyDir": empty_dir})
    return volumes


def _mount(path: str, read_only: bool = False, sub_path: str | None = None) -> dict[str, Any]:
    mount: dict[str, Any] = {"name": _volume_name(path), "mountPath": path}
    if read_only:
        mount["readOnly"] = True
    if sub_path is not None:
        mount["subPath"] = sub_path
    return mount


def _resources(cpu: str, memory: str, storage: str | None) -> dict[str, Any]:
    values = {"cpu": cpu, "memory": memory}
    if storage is not None:
        values["ephemeral-storage"] = storage
    return {"limits": dict(values), "requests": dict(values)}


def _tolerations(settings: K8sSettings) -> list[dict[str, Any]]:
    kept = [
        t
        for t in settings.tolerations
        if not (t.key in _NODE_LOSS_KEYS and t.effect in ("NoExecute", None))
    ]
    out = [t.to_manifest() for t in kept]
    for key in _NODE_LOSS_KEYS:
        out.append(
            Toleration(
                key=key,
                operator="Exists",
                effect="NoExecute",
                toleration_seconds=settings.node_loss_toleration_seconds,
            ).to_manifest()
        )
    return out


def _validate(spec: SandboxSpec, settings: K8sSettings) -> None:
    if not settings.namespace:
        raise ValueError("K8sSettings.namespace must not be empty")
    if settings.node_loss_toleration_seconds < 0:
        raise ValueError("K8sSettings.node_loss_toleration_seconds must not be negative")
    if settings.grader_gid <= 0:
        raise ValueError("K8sSettings.grader_gid must be above 0")
    if not settings.runtime_class_name and settings.runtime_kind != "runc":
        raise ValueError(f"runtime kind {settings.runtime_kind!r} requires a runtime class name")
    if spec.host_mounts:
        raise ValueError("a Lab_Pod never mounts host paths")
    paths = {m.path for m in spec.tmpfs_mounts}
    missing = {_WORKSPACE, "/home/learner", "/tmp", _GRADER} - paths
    if missing:
        raise ValueError(f"SandboxSpec lacks writable mounts: {sorted(missing)}")


def build_lab_pod(spec: SandboxSpec, settings: K8sSettings) -> dict[str, Any]:
    """Build the Lab_Pod manifest for ``spec`` (layout B of ADR 0009)."""

    _validate(spec, settings)
    helper_cpu = f"{_cfg.K8S_HELPER_CPU_MILLI}m"
    helper_memory = str(_cfg.K8S_HELPER_MEMORY_BYTES)
    grader_cpu = f"{_cfg.K8S_GRADER_CPU_MILLI}m"
    grader_memory = str(_cfg.K8S_GRADER_MEMORY_BYTES)

    containers: list[dict[str, Any]] = [
        {
            "name": CONTAINER_LEARNER,
            "image": spec.image,
            "command": list(_REAPING_INIT),
            "securityContext": _security_context(LEARNER_UID, spec.read_only_root),
            "resources": _resources(
                str(spec.cpu_limit),
                str(spec.memory_limit_bytes),
                str(spec.writable_quota_bytes),
            ),
            "volumeMounts": [_mount(_WORKSPACE), _mount("/home/learner"), _mount("/tmp")],
        },
        {
            "name": CONTAINER_GRADER,
            "image": spec.image,
            "command": list(_REAPING_INIT),
            "securityContext": _security_context(GRADER_UID, spec.read_only_root),
            "resources": _resources(grader_cpu, grader_memory, None),
            "volumeMounts": [
                _mount(_WORKSPACE, read_only=True),
                _mount(_GRADER, sub_path=GRADER_SUBPATH),
            ],
        },
        {
            "name": CONTAINER_OWNER,
            "image": spec.image,
            "command": list(_REAPING_INIT),
            "securityContext": _owner_context(spec.read_only_root, settings.grader_gid),
            "resources": _resources(helper_cpu, helper_memory, None),
            "volumeMounts": [_mount(_GRADER, sub_path=GRADER_SUBPATH)],
        },
    ]

    # The kubelet creates an emptyDir root as root:fsGroup, mode 2777. A non-root owner can never
    # ``chmod`` it (EPERM, no CAP_CHOWN or CAP_FOWNER), and the grader could write to it. The
    # init container, running as the owner UID, creates the directory that both containers mount
    # as ``/grader`` (``subPath``). That directory belongs to the owner and carries the grader
    # group, so ``GradeRun`` can open it to the group and close it again without any capability.
    init_containers: list[dict[str, Any]] = [
        {
            "name": INIT_GRADER_DIR,
            "image": spec.image,
            "command": ["/bin/mkdir", "-m", GRADER_DIR_MODE, f"{_GRADER}/{GRADER_SUBPATH}"],
            "securityContext": _owner_context(spec.read_only_root, settings.grader_gid),
            "resources": _resources(helper_cpu, helper_memory, None),
            "volumeMounts": [_mount(_GRADER)],
        }
    ]

    pod_spec: dict[str, Any] = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "shareProcessNamespace": False,
        "activeDeadlineSeconds": int(_cfg.LAB_SESSION_TTL.total_seconds()),
        "securityContext": {
            "runAsNonRoot": True,
            "fsGroup": settings.grader_gid,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "nodeSelector": dict(settings.node_selector),
        "tolerations": _tolerations(settings),
        "initContainers": init_containers,
        "containers": containers,
        "volumes": _volumes(spec.tmpfs_mounts),
    }
    if settings.runtime_class_name:
        pod_spec["runtimeClassName"] = settings.runtime_class_name
    if settings.priority_class_name:
        pod_spec["priorityClassName"] = settings.priority_class_name

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name(spec.session_stable_id),
            "namespace": settings.namespace,
            "labels": dict(spec.labels),
            "annotations": {ANNOTATION_PID_LIMIT: str(spec.pid_limit)},
        },
        "spec": pod_spec,
    }
