"""A ``K8sApiPort`` over the ``kubectl`` CLI, for the kind contract test (task 6.22).

The production client adapter (``edu_it_infrastructure.sandbox.k8s_client``) is a separate piece.
This module only lets the real-API contract test and the kind provider suites talk to a kind API
server through the same port as ``FakeK8sApi``, with no Kubernetes Python client installed.

``kubectl`` prints API failures as ``Error from server (<Reason>): <message>``; this adapter turns
that into :class:`K8sApiError` with the HTTP status of the reason, which is all the provider and
``classify_create_rejection`` read. Unreachable servers become ``ConnectionError``.

Deletes use ``--grace-period=0`` without ``--force``: kubectl then sends a grace period of 1, so
the pod stays visible with a ``deletionTimestamp`` until the kubelet has stopped it, the behavior
``FakeK8sApi`` models with ``delete_delay``.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from edu_it_infrastructure.sandbox.k8s_api import (
    PHASE_RUNNING,
    ExecOutput,
    K8sApiError,
    K8sStreamClosed,
    PodView,
)

__all__ = ["KubectlK8sApi", "parse_kubectl_error", "pod_view_from_json"]

_STATUS_OF_REASON: Final[dict[str, int]] = {
    "BadRequest": 400,
    "Unauthorized": 401,
    "Forbidden": 403,
    "NotFound": 404,
    "AlreadyExists": 409,
    "Conflict": 409,
    "Invalid": 422,
    "Timeout": 504,
    "ServerTimeout": 504,
    "ServiceUnavailable": 503,
    "InternalError": 500,
}
_SERVER_ERROR = re.compile(r"Error from server \((?P<reason>\w+)\):\s*(?P<message>.*)", re.DOTALL)
_UNREACHABLE = (
    "Unable to connect to the server",
    "connection refused",
    "i/o timeout",
    "the server is currently unable to handle the request",
)
#: The container runtime refused to start the exec process (for example the pod PID limit is used).
_EXEC_START_FAILED = ("failed to start exec", "failed to exec in container")
_NOT_RUNNING = (
    "unable to upgrade connection",
    "container not found",
    "cannot exec in a stopped state",
    "is not running",
    "container is not running",
)
_EOF = object()


def parse_kubectl_error(stderr: str) -> Exception | None:
    """Turn ``kubectl`` stderr into the port's exception, or ``None`` when it is not an API error."""

    match = _SERVER_ERROR.search(stderr)
    if match:
        reason = match["reason"]
        message = match["message"].strip()
        # ``kubectl create`` prefixes the Status message with 'error when creating "STDIN": '.
        message = re.sub(r'^error when \w+ "[^"]*":\s*', "", message)
        return K8sApiError(_STATUS_OF_REASON.get(reason, 500), reason, message)
    if any(marker in stderr for marker in _UNREACHABLE):
        return ConnectionError(stderr.strip())
    return None


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def pod_view_from_json(pod: Mapping[str, Any]) -> PodView:
    """Build a :class:`PodView` from a Pod object as the API server returns it."""

    metadata = pod.get("metadata") or {}
    status = pod.get("status") or {}
    spec = pod.get("spec") or {}
    conditions = {c.get("type"): c for c in status.get("conditions") or ()}
    scheduled = conditions.get("PodScheduled") or {}
    unschedulable = (
        scheduled.get("status") == "False" and scheduled.get("reason") == "Unschedulable"
    )
    reason = status.get("reason") or ("Unschedulable" if unschedulable else "")
    message = status.get("message") or (scheduled.get("message", "") if unschedulable else "")
    return PodView(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        labels=dict(metadata.get("labels") or {}),
        annotations=dict(metadata.get("annotations") or {}),
        phase=status.get("phase", "Pending"),
        ready=(conditions.get("Ready") or {}).get("status") == "True",
        unschedulable=unschedulable,
        created_at=_timestamp(metadata.get("creationTimestamp")) or datetime.now(UTC),
        deletion_timestamp=_timestamp(metadata.get("deletionTimestamp")),
        reason=reason,
        message=message,
        node_name=spec.get("nodeName"),
        container_names=tuple(c.get("name", "") for c in spec.get("containers") or ()),
    )


class _KubectlTerminal:
    """TTY-less byte stream over one ``kubectl exec -i`` process."""

    def __init__(self, argv: list[str]) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._chunks: queue.Queue[object] = queue.Queue()
        self._pending = bytearray()
        self._eof = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        while chunk := self._proc.stdout.read1(4096):  # type: ignore[attr-defined]
            self._chunks.put(chunk)
        self._chunks.put(_EOF)

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        if not self._pending and not self._eof:
            try:
                item = self._chunks.get(timeout=timeout_s)
            except queue.Empty:
                return b""
            if item is _EOF:
                self._eof = True
            else:
                self._pending.extend(item)  # type: ignore[arg-type]
        if self._pending:
            data = bytes(self._pending[:max_bytes])
            del self._pending[:max_bytes]
            return data
        raise K8sStreamClosed("stream closed")

    def write(self, data: bytes) -> None:
        if self._proc.poll() is not None or self._proc.stdin is None:
            raise K8sStreamClosed("stream closed")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise K8sStreamClosed("stream closed") from None

    def resize(self, rows: int, cols: int) -> None:
        if self._proc.poll() is not None:
            raise K8sStreamClosed("stream closed")

    def close(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass


class KubectlK8sApi:
    """:class:`K8sApiPort` implemented with ``kubectl --context <context>``."""

    def __init__(self, context: str, *, request_timeout_s: int = 30) -> None:
        self._context = context
        self._timeout = request_timeout_s

    # ------------------------------------------------------------------ plumbing

    def _argv(self, *args: str) -> list[str]:
        return ["kubectl", "--context", self._context, f"--request-timeout={self._timeout}s", *args]

    def _run(self, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv, no shell
                self._argv(*args),
                input=stdin,
                capture_output=True,
                timeout=self._timeout + 30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError("kubectl timed out") from None

    def _checked(self, *args: str, stdin: bytes | None = None) -> bytes:
        done = self._run(*args, stdin=stdin)
        if done.returncode != 0:
            stderr = done.stderr.decode("utf-8", "replace")
            raise parse_kubectl_error(stderr) or RuntimeError(stderr.strip())
        return done.stdout

    # ------------------------------------------------------------------ K8sApiPort

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> PodView:
        out = self._checked(
            "create", "-n", namespace, "-f", "-", "-o", "json", stdin=json.dumps(manifest).encode()
        )
        return pod_view_from_json(json.loads(out))

    def read_pod(self, namespace: str, name: str) -> PodView:
        out = self._checked("get", "pod", name, "-n", namespace, "-o", "json")
        return pod_view_from_json(json.loads(out))

    def delete_pod(self, namespace: str, name: str) -> None:
        self._checked("delete", "pod", name, "-n", namespace, "--grace-period=0", "--wait=false")

    def label_pod(self, namespace: str, name: str, labels: Mapping[str, str]) -> None:
        pairs = [f"{key}={value}" for key, value in labels.items()]
        self._checked("label", "pod", name, "-n", namespace, "--overwrite", *pairs)

    def list_pods(self, namespace: str, label_selector: str = "") -> Sequence[PodView]:
        args = ["get", "pods", "-n", namespace, "-o", "json"]
        if label_selector:
            args += ["-l", label_selector]
        items = json.loads(self._checked(*args)).get("items") or []
        return sorted((pod_view_from_json(i) for i in items), key=lambda p: p.name)

    def _require_running(self, namespace: str, name: str, container: str) -> None:
        view = self.read_pod(namespace, name)
        if container not in view.container_names:
            raise K8sApiError(
                400, "BadRequest", f"container {container} is not valid for pod {name}"
            )
        if view.phase != PHASE_RUNNING or view.deletion_timestamp is not None:
            raise K8sApiError(
                400, "BadRequest", f'container "{container}" in pod "{name}" is not running'
            )

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
        self._require_running(namespace, name, container)
        argv = self._argv("exec", "-n", namespace, name, "-c", container)
        if stdin is not None:
            argv.append("-i")
        argv += ["--", *command]
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
                input=stdin if stdin is not None else b"",
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            return ExecOutput(137, expired.stdout or b"", expired.stderr or b"", timed_out=True)
        stderr = done.stderr.decode("utf-8", "replace")
        if done.returncode != 0:
            error = parse_kubectl_error(stderr)
            if error is not None:
                raise error
            if any(marker in stderr for marker in _NOT_RUNNING):
                raise K8sApiError(400, "BadRequest", stderr.strip())
            if any(marker in stderr for marker in _EXEC_START_FAILED):
                # The API server answers the exec upgrade with a 500 Status (the runtime could not
                # start the process); kubectl prints it without the "Error from server" prefix.
                raise K8sApiError(500, "InternalError", stderr.strip())
        return ExecOutput(done.returncode, done.stdout, done.stderr)

    def open_terminal(
        self,
        namespace: str,
        name: str,
        container: str,
        command: Sequence[str],
    ) -> _KubectlTerminal:
        self._require_running(namespace, name, container)
        argv = self._argv("exec", "-i", "-n", namespace, name, "-c", container, "--", *command)
        return _KubectlTerminal(argv)
