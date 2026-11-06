"""The ``K8sApiPort`` adapter interface between the Kubernetes provider and the cluster.

``KubernetesSandboxProvider`` talks to the cluster only through this port. Two
implementations exist: ``FakeK8sApi`` (in-memory, driven by the injected ``Clock``) and the real
client adapter. The real-API contract test (kubernetes-deployment task 6.22) runs the same
scenarios on both, so every observable behavior is expressed here in plain values: no Kubernetes
client type crosses the port.

Failures are raised as :class:`K8sApiError`, which carries the HTTP ``status``, the ``reason`` and
the ``message`` of the API server Status object. These are exactly the inputs of
``classify_create_rejection`` and ``map_api_exception``. Connection failures are raised as the
built-in ``ConnectionError`` or ``TimeoutError``, which ``map_api_exception`` maps to
``ProviderUnavailable``. Raw API text stays in the error and is logged by the provider only.

Requirements: kubernetes-deployment 1.5, 2.11, 2.12, 22.8.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "PHASE_FAILED",
    "PHASE_PENDING",
    "PHASE_RUNNING",
    "PHASE_SUCCEEDED",
    "ExecOutput",
    "K8sApiError",
    "K8sApiPort",
    "K8sStreamClosed",
    "K8sTerminalStream",
    "PodView",
]

PHASE_PENDING: Final[str] = "Pending"
PHASE_RUNNING: Final[str] = "Running"
PHASE_SUCCEEDED: Final[str] = "Succeeded"
PHASE_FAILED: Final[str] = "Failed"


class K8sApiError(Exception):
    """A non-success answer of the API server (an HTTP Status object)."""

    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(f"{status} {reason}: {message}")
        self.status = status
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class PodView:
    """The part of a Pod the provider reads. Immutable snapshot at read time."""

    name: str
    namespace: str
    labels: Mapping[str, str]
    annotations: Mapping[str, str]
    phase: str
    """One of ``Pending``, ``Running``, ``Succeeded``, ``Failed``."""
    ready: bool
    """All containers ready (the Ready condition is ``True``)."""
    unschedulable: bool
    """Pending with a ``PodScheduled=False`` condition of reason ``Unschedulable``."""
    created_at: datetime
    deletion_timestamp: datetime | None
    """Set once a delete was accepted; the pod disappears when the grace period ends."""
    reason: str = ""
    """Status reason, e.g. ``Evicted`` or ``DeadlineExceeded``; empty when none."""
    message: str = ""
    node_name: str | None = None
    container_names: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ExecOutput:
    """Result of one command run in a container through the exec subresource."""

    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False


class K8sStreamClosed(Exception):
    """The exec / TTY stream is closed (the pod is gone, or the stream was closed)."""


@runtime_checkable
class K8sTerminalStream(Protocol):
    """Raw byte stream of a TTY exec. No Kubernetes client type crosses the port."""

    def read(self, max_bytes: int, timeout_s: float | None = None) -> bytes:
        """Return up to ``max_bytes`` (channel 1 and 2 data); empty bytes on timeout.

        Raises :class:`K8sStreamClosed` (or :class:`K8sApiError` 404) when the stream is closed
        or the pod is gone.
        """
        ...

    def write(self, data: bytes) -> None:
        """Send ``data`` on the stdin channel (0). Same errors as :meth:`read`."""
        ...

    def resize(self, rows: int, cols: int) -> None:
        """Send a resize message on the resize channel (4). Same errors as :meth:`read`."""
        ...

    def close(self) -> None:
        """Close the stream; idempotent and never raises."""
        ...


@runtime_checkable
class K8sApiPort(Protocol):
    """Pod operations the Kubernetes provider needs from the API server."""

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> PodView:
        """Create a pod from ``manifest``.

        Raises :class:`K8sApiError` with status 403 (quota, Pod Security, RuntimeClass),
        409 ``AlreadyExists`` or 404 (namespace missing).
        """
        ...

    def read_pod(self, namespace: str, name: str) -> PodView:
        """Return the pod. Raises :class:`K8sApiError` with status 404 when it does not exist."""
        ...

    def delete_pod(self, namespace: str, name: str) -> None:
        """Ask for the pod to be deleted; it stays visible, with a ``deletion_timestamp``,
        until its grace period ends. Raises :class:`K8sApiError` with status 404 when it
        does not exist. Deleting a pod that is already terminating succeeds. Adapters delete
        with grace period 0 (design, destroy semantics).
        """
        ...

    def label_pod(self, namespace: str, name: str, labels: Mapping[str, str]) -> None:
        """Merge ``labels`` into the pod labels (task 5.2: ``edu-it.io/destroy=pending``).

        Raises :class:`K8sApiError` with status 404 when the pod does not exist.
        """
        ...

    def list_pods(self, namespace: str, label_selector: str = "") -> Sequence[PodView]:
        """Return the pods matching ``label_selector`` (``k=v`` terms joined by commas; a bare
        ``k`` term matches the presence of the label), sorted by name.
        """
        ...

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
        """Run ``command`` in ``container`` and return its output.

        ``stdin`` is written to the process and then closed (EOF), which lets ``tar -x`` finish.
        When ``timeout_s`` expires the adapter stops waiting and returns ``timed_out=True`` with
        exit code 137; it cannot kill the remote process, so the caller runs a kill exec.

        Raises :class:`K8sApiError` with status 404 when the pod does not exist and 400 when
        the container is not running. A stream that closes mid-command raises an exception
        that ``map_api_exception`` maps to ``SandboxGone``.
        """
        ...

    def open_terminal(
        self,
        namespace: str,
        name: str,
        container: str,
        command: Sequence[str],
    ) -> K8sTerminalStream:
        """Start ``command`` with a TTY in ``container`` and return its byte stream.

        Raises :class:`K8sApiError` with status 404 when the pod does not exist and 400 when
        the container is not running.
        """
        ...
