"""``FakeK8sApi``: an in-memory :class:`K8sApiPort` driven by the injected clock.

It models what the Kubernetes provider depends on, so provider properties 6 to 10 and the
Sandbox_Port contract suite run without a cluster (kubernetes-deployment Req 1.5, 2.11, 2.12,
22.8):

- **Admission**, in the order of a real API server: RuntimeClass, Pod Security (``restricted``
  or ``privileged`` per namespace), ResourceQuota, then the 409 ``AlreadyExists`` check at storage.
  The error status, reason and message wording follow real API server answers, so
  ``classify_create_rejection`` classifies them as it does on a real cluster.
- **Quota**: hard limits on ``pods`` and on ``requests.``/``limits.`` of ``cpu``, ``memory`` and
  ``ephemeral-storage``. Pods in phase ``Succeeded`` or ``Failed`` do not count.
- **Scheduling**: nodes with labels, taints, readiness and a pod capacity; node selector and
  tolerations are honoured. A pod that fits no node stays ``Pending`` with ``unschedulable`` set,
  and is placed on a later call once a node fits (scheduling is evaluated lazily on each call).
- **Delays on the clock**: a pod becomes ``Running`` and ready ``start_delay`` after it is
  scheduled; a deleted pod stays visible with a ``deletion_timestamp`` for ``delete_delay`` (zero
  for a pod that was never scheduled); ``activeDeadlineSeconds`` fails the pod with reason
  ``DeadlineExceeded``. Every call also advances the clock by ``call_latency``. Nothing sleeps.
- **Faults**: ``fail_next`` makes the next call(s) of one operation raise a given exception, and
  ``set_unreachable`` makes every call raise ``ConnectionError``.

It imports no Kubernetes client, socket or subprocess module.
"""

from __future__ import annotations

import copy
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from edu_it_infrastructure.sandbox.k8s_api import (
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    PHASE_SUCCEEDED,
    ExecOutput,
    K8sApiError,
    K8sStreamClosed,
    PodView,
)

if TYPE_CHECKING:
    from edu_it_infrastructure.fakes.clock import Test_Clock

__all__ = [
    "ExecHandler",
    "FakeK8sApi",
    "FakeNode",
    "FakeTerminalStream",
    "Operation",
    "PodSecurityLevel",
    "Taint",
]

type Operation = Literal["create", "read", "delete", "list", "exec", "label", "terminal"]
type PodSecurityLevel = Literal["restricted", "privileged"]
type ExecHandler = Callable[[PodView, str, Sequence[str]], ExecOutput | None]
"""Scripted exec hook ``(pod, container, command)``; ``None`` falls through to the default."""

_KILLED_EXIT: Final[int] = 137
_TERMINAL_PHASES: Final[frozenset[str]] = frozenset({PHASE_SUCCEEDED, PHASE_FAILED})
_QUOTA_NAME: Final[str] = "lab-quota"

_BINARY = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
_DECIMAL = {"k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}


@dataclass(frozen=True, slots=True)
class Taint:
    key: str
    value: str = ""
    effect: Literal["NoSchedule", "PreferNoSchedule", "NoExecute"] = "NoSchedule"


@dataclass(slots=True)
class FakeNode:
    """A schedulable node. ``max_pods`` bounds the live pods placed on it."""

    name: str
    labels: dict[str, str] = field(default_factory=dict)
    taints: tuple[Taint, ...] = ()
    max_pods: int = 110
    ready: bool = True


@dataclass(slots=True)
class _Namespace:
    psa: PodSecurityLevel
    hard: dict[str, int]


@dataclass(slots=True)
class _PodRecord:
    manifest: dict[str, Any]
    created_at: datetime
    node: str | None = None
    scheduled_at: datetime | None = None
    deletion_at: datetime | None = None
    removal_at: datetime | None = None
    failed_reason: str = ""
    failed_message: str = ""


def parse_quantity(resource: str, text: str) -> int:
    """Parse a Kubernetes quantity: CPU in millicores, other resources in bytes."""

    raw = str(text).strip()
    if resource.endswith("cpu"):
        if raw.endswith("m"):
            return int(raw[:-1])
        return round(float(raw) * 1000)
    for suffix, factor in _BINARY.items():
        if raw.endswith(suffix):
            return int(float(raw[: -len(suffix)]) * factor)
    for suffix, factor in _DECIMAL.items():
        if raw.endswith(suffix):
            return int(float(raw[: -len(suffix)]) * factor)
    return int(float(raw))


def _matches_selector(labels: Mapping[str, str], selector: str) -> bool:
    for term in (t.strip() for t in selector.split(",") if t.strip()):
        if "!=" in term:
            key, _, value = term.partition("!=")
            if labels.get(key.strip()) == value.strip():
                return False
        elif "=" in term:
            key, _, value = term.partition("=")
            if labels.get(key.strip().rstrip("=")) != value.strip().lstrip("="):
                return False
        elif term not in labels:
            return False
    return True


def _tolerates(tolerations: Sequence[Mapping[str, Any]], taint: Taint) -> bool:
    for tol in tolerations:
        if tol.get("effect") not in (None, taint.effect):
            continue
        key = tol.get("key")
        operator = tol.get("operator", "Equal")
        if key is None:
            if operator == "Exists":
                return True
            continue
        if key != taint.key:
            continue
        if operator == "Exists" or tol.get("value", "") == taint.value:
            return True
    return False


class FakeTerminalStream:
    """In-memory TTY stream. Reads never block: an empty read advances the clock by its timeout.

    Output is supplied with :meth:`feed` (and echoed from ``write`` when the API's
    ``terminal_echo`` is true). Once the pod is gone or terminating, every operation raises
    :class:`K8sStreamClosed`, as a closed WebSocket does.
    """

    def __init__(
        self,
        api: FakeK8sApi,
        namespace: str,
        name: str,
        container: str,
        command: tuple[str, ...],
    ) -> None:
        self._api = api
        self.namespace = namespace
        self.name = name
        self.container = container
        self.command = command
        self.written: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.closed = False
        self._out = bytearray()

    def feed(self, data: bytes) -> None:
        """Make ``data`` available to the next ``read``."""

        self._out.extend(data)

    def _check(self) -> None:
        if self.closed:
            raise K8sStreamClosed("stream closed")
        self._api._stream_alive(self.namespace, self.name)

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        self._check()
        if not self._out:
            if timeout_s is not None:
                self._api._clock.advance(timedelta(seconds=timeout_s))
            return b""
        chunk = bytes(self._out[:max_bytes])
        del self._out[:max_bytes]
        return chunk

    def write(self, data: bytes) -> None:
        self._check()
        self.written.append(data)
        if self._api.terminal_echo:
            self._out.extend(data)

    def resize(self, rows: int, cols: int) -> None:
        self._check()
        self.sizes.append((rows, cols))

    def close(self) -> None:
        self.closed = True


class FakeK8sApi:
    """In-memory Kubernetes API for pods. Thread-safe; all time comes from ``clock``."""

    def __init__(
        self,
        clock: Test_Clock,
        *,
        start_delay: timedelta = timedelta(seconds=2),
        delete_delay: timedelta = timedelta(seconds=1),
        call_latency: timedelta = timedelta(0),
        exec_duration: timedelta = timedelta(0),
        nodes: Sequence[FakeNode] | None = None,
        runtime_classes: Sequence[str] = (),
        namespaces: Sequence[str] = ("labs",),
    ) -> None:
        self._clock = clock
        self.start_delay = start_delay
        self.delete_delay = delete_delay
        self.call_latency = call_latency
        self.exec_duration = exec_duration
        self.exec_handler: ExecHandler | None = None
        self.exec_calls: list[tuple[str, str, str, tuple[str, ...]]] = []
        #: Stdin of each ``exec_command`` call, parallel to ``exec_calls`` (``None`` when absent).
        self.exec_stdin: list[bytes | None] = []
        #: Every TTY stream opened through ``open_terminal``.
        self.terminals: list[FakeTerminalStream] = []
        #: When true, bytes written to a terminal are echoed back to its reader.
        self.terminal_echo = True
        self._lock = threading.RLock()
        self._nodes: dict[str, FakeNode] = {}
        self._runtime_classes: set[str] = set(runtime_classes)
        self._namespaces: dict[str, _Namespace] = {}
        self._pods: dict[tuple[str, str], _PodRecord] = {}
        self._faults: dict[str, deque[BaseException]] = defaultdict(deque)
        self._unreachable = False
        for node in nodes if nodes is not None else (FakeNode("node-1"),):
            self.add_node(node)
        for namespace in namespaces:
            self.add_namespace(namespace)

    # ------------------------------------------------------------------ cluster setup

    def add_node(self, node: FakeNode) -> None:
        with self._lock:
            self._nodes[node.name] = node

    def set_node_ready(self, name: str, ready: bool) -> None:
        with self._lock:
            self._nodes[name].ready = ready

    def add_runtime_class(self, name: str) -> None:
        with self._lock:
            self._runtime_classes.add(name)

    def remove_runtime_class(self, name: str) -> None:
        with self._lock:
            self._runtime_classes.discard(name)

    def add_namespace(
        self,
        name: str,
        *,
        psa: PodSecurityLevel = "restricted",
        hard: Mapping[str, str | int] | None = None,
    ) -> None:
        """Create a namespace. ``hard`` maps quota keys (``pods``, ``requests.cpu``,
        ``limits.memory``, ``requests.ephemeral-storage``, ...) to quantities."""

        with self._lock:
            self._namespaces[name] = _Namespace(psa=psa, hard={})
            if hard:
                self.set_quota(name, hard)

    def set_quota(self, namespace: str, hard: Mapping[str, str | int]) -> None:
        with self._lock:
            parsed = {
                key: parse_quantity(key, str(value)) if key != "pods" else int(value)
                for key, value in hard.items()
            }
            self._namespaces[namespace].hard = parsed

    # ------------------------------------------------------------------ fault injection

    def fail_next(self, operation: Operation, error: BaseException, times: int = 1) -> None:
        """Make the next ``times`` calls of ``operation`` raise ``error``."""

        with self._lock:
            self._faults[operation].extend([error] * times)

    def set_unreachable(self, unreachable: bool) -> None:
        with self._lock:
            self._unreachable = unreachable

    # ------------------------------------------------------------------ cluster events

    def evict_pod(self, namespace: str, name: str, reason: str = "Evicted") -> None:
        """Mark the pod ``Failed`` (node pressure eviction). It frees its quota but stays listed."""

        with self._lock:
            record = self._pods[(namespace, name)]
            record.failed_reason = reason
            record.failed_message = "The node was low on resource."

    def vanish_pod(self, namespace: str, name: str) -> None:
        """Remove the pod at once, as when its node is lost and the object is garbage collected."""

        with self._lock:
            self._pods.pop((namespace, name), None)

    # ------------------------------------------------------------------ K8sApiPort

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> PodView:
        with self._lock:
            self._enter("create")
            ns = self._namespace(namespace)
            body: dict[str, Any] = copy.deepcopy(dict(manifest))
            metadata: dict[str, Any] = body.get("metadata") or {}
            name = metadata.get("name")
            if not name:
                raise K8sApiError(422, "Invalid", "metadata.name: Required value")
            declared = metadata.get("namespace")
            if declared not in (None, namespace):
                raise K8sApiError(
                    400,
                    "BadRequest",
                    "the namespace of the provided object does not match the namespace "
                    "sent on the request",
                )
            self._admit_runtime_class(name, body)
            self._admit_pod_security(name, ns, body)
            self._admit_quota(name, namespace, ns, body)
            key = (namespace, name)
            if key in self._pods:
                raise K8sApiError(409, "AlreadyExists", f'pods "{name}" already exists')
            record = _PodRecord(manifest=body, created_at=self._clock.now())
            self._pods[key] = record
            self._sync()
            return self._view(namespace, name, record)

    def read_pod(self, namespace: str, name: str) -> PodView:
        with self._lock:
            self._enter("read")
            self._namespace(namespace)
            return self._view(namespace, name, self._get(namespace, name))

    def delete_pod(self, namespace: str, name: str) -> None:
        with self._lock:
            self._enter("delete")
            self._namespace(namespace)
            record = self._get(namespace, name)
            if record.deletion_at is not None:
                return
            now = self._clock.now()
            record.deletion_at = now
            delay = timedelta(0) if record.node is None else self.delete_delay
            record.removal_at = now + delay
            self._sync()

    def label_pod(self, namespace: str, name: str, labels: Mapping[str, str]) -> None:
        with self._lock:
            self._enter("label")
            self._namespace(namespace)
            record = self._get(namespace, name)
            metadata: dict[str, Any] = record.manifest.setdefault("metadata", {})
            metadata.setdefault("labels", {}).update(labels)

    def list_pods(self, namespace: str, label_selector: str = "") -> Sequence[PodView]:
        with self._lock:
            self._enter("list")
            self._namespace(namespace)
            return [
                self._view(namespace, name, record)
                for (ns, name), record in sorted(self._pods.items())
                if ns == namespace
                and _matches_selector(
                    record.manifest.get("metadata", {}).get("labels", {}), label_selector
                )
            ]

    def exec_command(
        self,
        namespace: str,
        name: str,
        container: str,
        command: Sequence[str],
        *,
        timeout_s: float | None = None,
        stdin: bytes | None = None,
    ) -> ExecOutput:
        with self._lock:
            self._enter("exec")
            view = self._exec_target(namespace, name, container)
            argv = tuple(command)
            self.exec_calls.append((namespace, name, container, argv))
            self.exec_stdin.append(stdin)
            handler_output = self.exec_handler(view, container, argv) if self.exec_handler else None
            output = handler_output if handler_output is not None else ExecOutput(exit_code=0)
            duration = self.exec_duration
            if timeout_s is not None and duration > timedelta(seconds=timeout_s):
                self._clock.advance(timedelta(seconds=timeout_s))
                return ExecOutput(_KILLED_EXIT, output.stdout, output.stderr, timed_out=True)
            self._clock.advance(duration)
            return output

    def open_terminal(
        self,
        namespace: str,
        name: str,
        container: str,
        command: Sequence[str],
    ) -> FakeTerminalStream:
        with self._lock:
            self._enter("terminal")
            self._exec_target(namespace, name, container)
            stream = FakeTerminalStream(self, namespace, name, container, tuple(command))
            self.terminals.append(stream)
            return stream

    # ------------------------------------------------------------------ introspection

    def pod_names(self, namespace: str) -> list[str]:
        """Names of every pod object still stored (including terminating ones)."""

        with self._lock:
            self._sync()
            return sorted(n for (ns, n) in self._pods if ns == namespace)

    # ------------------------------------------------------------------ internals

    def _enter(self, operation: Operation) -> None:
        if self._unreachable:
            raise ConnectionError("kubernetes API server is unreachable")
        if self.call_latency > timedelta(0):
            self._clock.advance(self.call_latency)
        faults = self._faults[operation]
        if faults:
            raise faults.popleft()
        self._sync()

    def _exec_target(self, namespace: str, name: str, container: str) -> PodView:
        """The pod an exec / attach request addresses, with the API server's errors."""

        self._namespace(namespace)
        view = self._view(namespace, name, self._get(namespace, name))
        if container not in view.container_names:
            raise K8sApiError(
                400, "BadRequest", f"container {container} is not valid for pod {name}"
            )
        if view.phase != PHASE_RUNNING or view.deletion_timestamp is not None:
            raise K8sApiError(
                400, "BadRequest", f'container "{container}" in pod "{name}" is not running'
            )
        return view

    def _stream_alive(self, namespace: str, name: str) -> None:
        """Raise the error an open TTY stream sees once its pod is gone or going."""

        with self._lock:
            self._sync()
            record = self._pods.get((namespace, name))
            if record is None:
                raise K8sStreamClosed("pod is gone")
            if record.deletion_at is not None or self._phase(record) != PHASE_RUNNING:
                raise K8sStreamClosed("pod is not running")

    def _namespace(self, namespace: str) -> _Namespace:
        try:
            return self._namespaces[namespace]
        except KeyError:
            raise K8sApiError(404, "NotFound", f'namespaces "{namespace}" not found') from None

    def _get(self, namespace: str, name: str) -> _PodRecord:
        try:
            return self._pods[(namespace, name)]
        except KeyError:
            raise K8sApiError(404, "NotFound", f'pods "{name}" not found') from None

    # admission ------------------------------------------------------------

    def _admit_runtime_class(self, name: str, body: Mapping[str, Any]) -> None:
        runtime_class = (body.get("spec") or {}).get("runtimeClassName")
        if runtime_class and runtime_class not in self._runtime_classes:
            raise K8sApiError(
                403,
                "Forbidden",
                f'pods "{name}" is forbidden: pod rejected: '
                f'RuntimeClass "{runtime_class}" not found',
            )

    def _admit_pod_security(self, name: str, ns: _Namespace, body: Mapping[str, Any]) -> None:
        if ns.psa != "restricted":
            return
        spec = body.get("spec") or {}
        pod_context = spec.get("securityContext") or {}
        problems: list[str] = []
        for flag in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(flag):
                problems.append(f"{flag}=true")
        if any("hostPath" in volume for volume in spec.get("volumes") or ()):
            problems.append("restricted volume types (hostPath)")
        for container in (*(spec.get("initContainers") or ()), *(spec.get("containers") or ())):
            ctx = container.get("securityContext") or {}
            cname = container.get("name", "?")
            if ctx.get("privileged"):
                problems.append(f'privileged (container "{cname}" must not set privileged=true)')
            if ctx.get("allowPrivilegeEscalation") is not False:
                problems.append(
                    f'allowPrivilegeEscalation != false (container "{cname}" must set '
                    "securityContext.allowPrivilegeEscalation=false)"
                )
            if "ALL" not in ((ctx.get("capabilities") or {}).get("drop") or ()):
                problems.append(
                    f'unrestricted capabilities (container "{cname}" must set '
                    'securityContext.capabilities.drop=["ALL"])'
                )
            non_root = ctx.get("runAsNonRoot", pod_context.get("runAsNonRoot"))
            if non_root is not True:
                problems.append(
                    f'runAsNonRoot != true (pod or container "{cname}" must set '
                    "securityContext.runAsNonRoot=true)"
                )
            seccomp = (ctx.get("seccompProfile") or pod_context.get("seccompProfile") or {}).get(
                "type"
            )
            if seccomp not in ("RuntimeDefault", "Localhost"):
                problems.append(
                    f'seccompProfile (pod or container "{cname}" must set '
                    'securityContext.seccompProfile.type to "RuntimeDefault" or "Localhost")'
                )
        if problems:
            raise K8sApiError(
                403,
                "Forbidden",
                f'pods "{name}" is forbidden: violates PodSecurity "restricted:latest": '
                + ", ".join(problems),
            )

    def _admit_quota(
        self, name: str, namespace: str, ns: _Namespace, body: Mapping[str, Any]
    ) -> None:
        if not ns.hard:
            return
        requested = self._usage_of(body)
        used: dict[str, int] = defaultdict(int)
        for (pod_ns, _), record in self._pods.items():
            if pod_ns == namespace and self._phase(record) not in _TERMINAL_PHASES:
                for key, amount in self._usage_of(record.manifest).items():
                    used[key] += amount
        exceeded = [
            key for key, limit in ns.hard.items() if used[key] + requested.get(key, 0) > limit
        ]
        if exceeded:
            detail = ", ".join(
                f"requested: {k}={requested.get(k, 0)}, used: {k}={used[k]}, "
                f"limited: {k}={ns.hard[k]}"
                for k in exceeded
            )
            raise K8sApiError(
                403,
                "Forbidden",
                f'pods "{name}" is forbidden: exceeded quota: {_QUOTA_NAME}, {detail}',
            )

    @staticmethod
    def _usage_of(body: Mapping[str, Any]) -> dict[str, int]:
        usage: dict[str, int] = {"pods": 1}
        for container in (body.get("spec") or {}).get("containers") or ():
            resources = container.get("resources") or {}
            for kind in ("requests", "limits"):
                for resource, text in (resources.get(kind) or {}).items():
                    key = f"{kind}.{resource}"
                    usage[key] = usage.get(key, 0) + parse_quantity(resource, text)
        return usage

    # state machine --------------------------------------------------------

    def _sync(self) -> None:
        """Advance every pod to the state implied by the current clock time."""

        now = self._clock.now()
        for key, record in list(self._pods.items()):
            if record.removal_at is not None and now >= record.removal_at:
                del self._pods[key]
                continue
            if record.node is None and record.deletion_at is None:
                node = self._pick_node(record)
                if node is not None:
                    record.node = node
                    record.scheduled_at = now
            deadline = (record.manifest.get("spec") or {}).get("activeDeadlineSeconds")
            if (
                deadline is not None
                and not record.failed_reason
                and now >= record.created_at + timedelta(seconds=int(deadline))
            ):
                record.failed_reason = "DeadlineExceeded"
                record.failed_message = (
                    "Pod was active on the node longer than the specified deadline"
                )

    def _pick_node(self, record: _PodRecord) -> str | None:
        spec = record.manifest.get("spec") or {}
        selector = spec.get("nodeSelector") or {}
        tolerations = spec.get("tolerations") or ()
        for node in sorted(self._nodes.values(), key=lambda n: n.name):
            if not node.ready:
                continue
            if any(node.labels.get(k) != v for k, v in selector.items()):
                continue
            if any(
                t.effect in ("NoSchedule", "NoExecute") and not _tolerates(tolerations, t)
                for t in node.taints
            ):
                continue
            placed = sum(
                1
                for other in self._pods.values()
                if other.node == node.name and self._phase(other) not in _TERMINAL_PHASES
            )
            if placed >= node.max_pods:
                continue
            return node.name
        return None

    def _phase(self, record: _PodRecord) -> str:
        if record.failed_reason:
            return PHASE_FAILED
        if record.node is None or record.scheduled_at is None:
            return PHASE_PENDING
        if self._clock.now() >= record.scheduled_at + self.start_delay:
            return PHASE_RUNNING
        return PHASE_PENDING

    def _view(self, namespace: str, name: str, record: _PodRecord) -> PodView:
        phase = self._phase(record)
        metadata = record.manifest.get("metadata") or {}
        unschedulable = record.node is None and not record.failed_reason
        message = ""
        if unschedulable:
            message = (
                f"0/{len(self._nodes)} nodes are available: no node matches the pod's node "
                "selector, taints or capacity."
            )
        return PodView(
            name=name,
            namespace=namespace,
            labels=dict(metadata.get("labels") or {}),
            annotations=dict(metadata.get("annotations") or {}),
            phase=phase,
            ready=phase == PHASE_RUNNING and record.deletion_at is None,
            unschedulable=unschedulable,
            created_at=record.created_at,
            deletion_timestamp=record.deletion_at,
            reason=record.failed_reason or ("Unschedulable" if unschedulable else ""),
            message=record.failed_message or message,
            node_name=record.node,
            container_names=tuple(
                c.get("name", "")
                for c in (record.manifest.get("spec") or {}).get("containers") or ()
            ),
        )
