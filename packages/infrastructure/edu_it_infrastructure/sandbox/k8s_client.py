"""Production ``K8sApiPort`` adapter over the official Kubernetes Python client
(kubernetes-deployment task 7, ADR 0009 "Backend selection").

``composition._build_kubernetes_provider`` imports this module lazily
(``edu_it_infrastructure.sandbox.k8s_client``) and calls :func:`build_k8s_api` only when
``SANDBOX_PROVIDER=kubernetes`` and no ``k8s_api`` is injected, so nothing imports the
``kubernetes`` package unless the backend is selected.

Three adapters implement :class:`K8sApiPort`: ``FakeK8sApi`` (unit tests), ``KubectlK8sApi`` (the
original kind harness, test code only) and :class:`K8sClientApi` here (the cluster). The real-API
contract test (task 6.22) runs the same scenarios on each.

Authentication
    :func:`build_k8s_api` loads the in-cluster configuration, which reads the ServiceAccount token
    and CA bundle at ``/var/run/secrets/kubernetes.io/serviceaccount``. The Chart sets
    ``automountServiceAccountToken: false`` (Req 4.6), so the Api and Worker Deployments project a
    short-lived ``serviceAccountToken`` volume at that path. The client re-reads the file when the
    token nears expiry. Outside a cluster the load raises ``ConfigException`` and the start is
    refused; the adapter never falls back to a kubeconfig. :func:`build_k8s_api_from_kubeconfig`
    exists for the kind test suites only and is never reached from the composition root.

Error mapping (identical to the fake and ``KubectlK8sApi``)
    - ``ApiException`` becomes :class:`K8sApiError` with the ``status``, ``reason`` and
      ``message`` of the API server Status object (``AlreadyExists``, ``Forbidden``, ...), which
      are exactly the inputs of ``classify_create_rejection``.
    - A websocket upgrade the server refuses (the client reports it as ``ApiException`` with
      status 0 and the handshake text) becomes :class:`K8sApiError` with the handshake status.
    - An unreachable API server becomes the built-in ``ConnectionError`` / ``TimeoutError``.
    - A stream that closes without an exit Status becomes :class:`K8sStreamClosed`.

No Kubernetes client type crosses the port.

Requirements: kubernetes-deployment 1.1, 1.5, 1.9, 1.10, 1.11, 1.12, 2.11, 2.12, 5.1, 5.2, 5.6,
22.1, 22.8.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import re
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from edu_it_infrastructure.sandbox.k8s_api import (
    PHASE_PENDING,
    PHASE_RUNNING,
    ExecOutput,
    K8sApiError,
    K8sStreamClosed,
    PodView,
)

if TYPE_CHECKING:
    from edu_it_infrastructure.composition import CompositionSettings

__all__ = [
    "DELETE_GRACE_SECONDS",
    "K8sClientApi",
    "build_k8s_api",
    "build_k8s_api_from_kubeconfig",
    "pod_view_from_v1pod",
    "translate_exception",
]

_LOG = logging.getLogger(__name__)

#: Per-request timeout of plain (non-stream) API calls.
REQUEST_TIMEOUT_S: Final[float] = 30.0
#: Grace period of a pod delete. 0 would remove the object at once and leave the containers
#: running on the node; 1 keeps the pod visible, with a ``deletionTimestamp``, until the kubelet
#: has stopped it. This is what ``kubectl delete --grace-period=0`` sends and what the fake models.
DELETE_GRACE_SECONDS: Final[int] = 1
#: How long one ``update`` waits for a frame when the caller set no deadline.
_POLL_SLICE_S: Final[float] = 1.0

_EXEC_CHANNEL_STDOUT: Final[int] = 1
_EXEC_CHANNEL_STDERR: Final[int] = 2
_EXEC_CHANNEL_ERROR: Final[int] = 3
_EXEC_CHANNEL_RESIZE: Final[int] = 4

_HANDSHAKE = re.compile(r"Handshake status (?P<status>\d{3})\s*(?P<phrase>[^-\n{]*)")
_STATUS_REASON = re.compile(r'"reason"\s*:\s*"(?P<reason>[^"]*)"')
_STATUS_MESSAGE = re.compile(r'"message"\s*:\s*"(?P<message>(?:[^"\\]|\\.)*)"')
_NO_HTTP_PHRASE_REASON: Final[dict[int, str]] = {
    400: "BadRequest",
    401: "Unauthorized",
    403: "Forbidden",
    404: "NotFound",
    409: "AlreadyExists",
    422: "Invalid",
    500: "InternalError",
    503: "ServiceUnavailable",
}
_EOF = object()


# ---------------------------------------------------------------------- error translation


def _status_object(body: object) -> tuple[str, str] | None:
    """``(reason, message)`` of a JSON Status object in ``body``, or ``None``."""

    if not isinstance(body, str | bytes):
        return None
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    if not text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return str(data.get("reason") or ""), str(data.get("message") or "")


def _from_handshake(text: str) -> K8sApiError | Exception:
    """Translate the text of a failed websocket upgrade (client status 0)."""

    match = _HANDSHAKE.search(text)
    if match is None:
        if "timed out" in text.lower():
            return TimeoutError(text)
        return ConnectionError(text)
    status = int(match["status"])
    reason_match = _STATUS_REASON.search(text)
    message_match = _STATUS_MESSAGE.search(text)
    reason = (reason_match["reason"] if reason_match else "") or _NO_HTTP_PHRASE_REASON.get(
        status, ""
    )
    if message_match:
        message = message_match["message"].encode("utf-8").decode("unicode_escape", "replace")
    else:
        message = match["phrase"].strip() or reason
    return K8sApiError(status, reason, message)


def translate_exception(exc: BaseException) -> Exception:
    """Map a raw client exception to the exception contract of :class:`K8sApiPort`."""

    from kubernetes.client.exceptions import ApiException
    from urllib3.exceptions import HTTPError

    if isinstance(exc, K8sApiError | K8sStreamClosed):
        return exc
    if isinstance(exc, ApiException):
        if not exc.status:  # 0: the websocket path wraps every failure like this
            return _from_handshake(str(exc.reason or ""))
        parsed = _status_object(exc.body)
        reason, message = parsed if parsed else ("", "")
        return K8sApiError(
            exc.status, reason or str(exc.reason or ""), message or str(exc.reason or "")
        )
    if isinstance(exc, ConnectionError | TimeoutError | HTTPError):
        if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
            return TimeoutError(str(exc))
        return ConnectionError(str(exc))
    if isinstance(exc, Exception):
        return exc
    raise exc


# ---------------------------------------------------------------------- pod view


def pod_view_from_v1pod(pod: Any) -> PodView:
    """Build a :class:`PodView` from a ``kubernetes.client.V1Pod``."""

    metadata = pod.metadata
    status = pod.status
    spec = pod.spec
    conditions = {c.type: c for c in (status.conditions or [])} if status else {}
    scheduled = conditions.get("PodScheduled")
    unschedulable = bool(
        scheduled and scheduled.status == "False" and scheduled.reason == "Unschedulable"
    )
    ready = conditions.get("Ready")
    reason = (status.reason if status else None) or ("Unschedulable" if unschedulable else "")
    message = (status.message if status else None) or (
        (scheduled.message or "") if unschedulable and scheduled else ""
    )
    return PodView(
        name=metadata.name or "",
        namespace=metadata.namespace or "",
        labels=dict(metadata.labels or {}),
        annotations=dict(metadata.annotations or {}),
        phase=(status.phase if status else None) or PHASE_PENDING,
        ready=bool(ready and ready.status == "True"),
        unschedulable=unschedulable,
        created_at=metadata.creation_timestamp or datetime.now(UTC),
        deletion_timestamp=metadata.deletion_timestamp,
        reason=reason,
        message=message,
        node_name=spec.node_name if spec else None,
        container_names=tuple(c.name for c in ((spec.containers if spec else None) or [])),
    )


# ---------------------------------------------------------------------- terminal


class _ClientTerminal:
    """Byte stream of one TTY ``pods/exec`` session over the client's ``WSClient``.

    A pump thread drains stdout and stderr into a queue, so ``read`` honors its timeout and a
    closed socket ends the stream with :class:`K8sStreamClosed` once the queue is empty.
    """

    def __init__(self, ws_client: Any) -> None:
        self._ws = ws_client
        self._chunks: queue.Queue[object] = queue.Queue()
        self._pending = bytearray()
        self._eof = False
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            while self._ws.is_open():
                self._ws.update(timeout=_POLL_SLICE_S)
                for channel in (_EXEC_CHANNEL_STDOUT, _EXEC_CHANNEL_STDERR):
                    data = self._ws.read_channel(channel, timeout=0)
                    if data:
                        self._chunks.put(bytes(data))
        except Exception:
            _LOG.debug("terminal stream ended", exc_info=True)
        finally:
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
        if not self._ws.is_open():
            raise K8sStreamClosed("stream closed")
        try:
            self._ws.write_stdin(bytes(data))
        except Exception:
            raise K8sStreamClosed("stream closed") from None

    def resize(self, rows: int, cols: int) -> None:
        if not self._ws.is_open():
            raise K8sStreamClosed("stream closed")
        payload = json.dumps({"Width": cols, "Height": rows}).encode("ascii")
        try:
            self._ws.write_channel(_EXEC_CHANNEL_RESIZE, payload)
        except Exception:
            raise K8sStreamClosed("stream closed") from None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._ws.close()


# ---------------------------------------------------------------------- exec result


def exit_code_of(error_channel: bytes | str | None) -> int:
    """Exit code carried by the Status object on the exec error channel.

    ``Success`` is 0; ``NonZeroExitCode`` carries the code in ``details.causes``. An empty channel
    means the stream closed without a result (the pod or the connection was lost), which raises
    :class:`K8sStreamClosed`. Any other failure Status is the runtime failing to start the process
    (for example the pod PID limit): it raises :class:`K8sApiError` 500, which the exec adapter
    retries as a start failure.
    """

    text = (
        error_channel.decode("utf-8", "replace")
        if isinstance(error_channel, bytes)
        else (error_channel or "")
    )
    if not text.strip():
        raise K8sStreamClosed("stream closed without an exit status")
    try:
        status = json.loads(text)
    except ValueError:
        raise K8sStreamClosed("stream closed with an unreadable exit status") from None
    if status.get("status") == "Success":
        return 0
    for cause in (status.get("details") or {}).get("causes") or ():
        if cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message", ""))
            except (TypeError, ValueError):
                break
    raise K8sApiError(500, "InternalError", str(status.get("message") or "exec failed"))


# ---------------------------------------------------------------------- the port


class K8sClientApi:
    """:class:`K8sApiPort` over ``kubernetes.client.CoreV1Api``."""

    def __init__(self, core_v1: Any, *, request_timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._core_v1 = core_v1
        self._timeout = request_timeout_s

    def create_pod(self, namespace: str, manifest: Mapping[str, object]) -> PodView:
        try:
            pod = self._core_v1.create_namespaced_pod(
                namespace, dict(manifest), _request_timeout=self._timeout
            )
        except Exception as exc:
            raise translate_exception(exc) from None
        return pod_view_from_v1pod(pod)

    def read_pod(self, namespace: str, name: str) -> PodView:
        try:
            pod = self._core_v1.read_namespaced_pod(name, namespace, _request_timeout=self._timeout)
        except Exception as exc:
            raise translate_exception(exc) from None
        return pod_view_from_v1pod(pod)

    def delete_pod(self, namespace: str, name: str) -> None:
        from kubernetes.client import V1DeleteOptions

        try:
            self._core_v1.delete_namespaced_pod(
                name,
                namespace,
                body=V1DeleteOptions(grace_period_seconds=DELETE_GRACE_SECONDS),
                _request_timeout=self._timeout,
            )
        except Exception as exc:
            raise translate_exception(exc) from None

    def label_pod(self, namespace: str, name: str, labels: Mapping[str, str]) -> None:
        body = {"metadata": {"labels": dict(labels)}}
        try:
            self._core_v1.patch_namespaced_pod(
                name, namespace, body, _request_timeout=self._timeout
            )
        except Exception as exc:
            raise translate_exception(exc) from None

    def list_pods(self, namespace: str, label_selector: str = "") -> Sequence[PodView]:
        try:
            result = self._core_v1.list_namespaced_pod(
                namespace,
                label_selector=label_selector or "",
                _request_timeout=self._timeout,
            )
        except Exception as exc:
            raise translate_exception(exc) from None
        return sorted((pod_view_from_v1pod(item) for item in result.items), key=lambda p: p.name)

    def _require_running(self, namespace: str, name: str, container: str) -> None:
        """404 for a missing pod, 400 for a container that cannot be exec'd into.

        The API server answers an exec into a terminating or not-yet-running pod in several
        runtime-dependent ways; one read gives the single answer the fake and the kubectl adapter
        give.
        """

        view = self.read_pod(namespace, name)
        if container not in view.container_names:
            raise K8sApiError(
                400, "BadRequest", f"container {container} is not valid for pod {name}"
            )
        if view.phase != PHASE_RUNNING or view.deletion_timestamp is not None:
            raise K8sApiError(
                400, "BadRequest", f'container "{container}" in pod "{name}" is not running'
            )

    def _open_exec(
        self, namespace: str, name: str, container: str, command: Sequence[str], *, stdin: bool,
        tty: bool,
    ) -> Any:  # fmt: skip
        from kubernetes.stream import stream

        self._require_running(namespace, name, container)
        try:
            return stream(
                self._core_v1.connect_get_namespaced_pod_exec,
                name,
                namespace,
                container=container,
                command=list(command),
                stdin=stdin,
                stdout=True,
                stderr=not tty,
                tty=tty,
                binary=True,
                _preload_content=False,
            )
        except Exception as exc:
            raise translate_exception(exc) from None

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
        ws = self._open_exec(
            namespace, name, container, command, stdin=stdin is not None, tty=False
        )
        try:
            if stdin is not None:
                ws.write_stdin(stdin)
                # v5 half-close, so ``tar -x`` sees EOF; a v4 server ignores it (no-op).
                ws.close_channel(0)
            ws.run_forever(timeout=None if timeout_s is None else max(timeout_s, 0.001))
            stdout = bytes(ws.read_channel(_EXEC_CHANNEL_STDOUT, timeout=0) or b"")
            stderr = bytes(ws.read_channel(_EXEC_CHANNEL_STDERR, timeout=0) or b"")
            if ws.is_open():
                # The deadline passed with the command still running; the caller runs a kill exec.
                return ExecOutput(137, stdout, stderr, timed_out=True)
            code = exit_code_of(ws.read_channel(_EXEC_CHANNEL_ERROR, timeout=0))
            return ExecOutput(code, stdout, stderr)
        except Exception as exc:
            raise translate_exception(exc) from None
        finally:
            with contextlib.suppress(Exception):
                ws.close()

    def open_terminal(
        self,
        namespace: str,
        name: str,
        container: str,
        command: Sequence[str],
    ) -> _ClientTerminal:
        return _ClientTerminal(
            self._open_exec(namespace, name, container, command, stdin=True, tty=True)
        )


# ---------------------------------------------------------------------- builders


def build_k8s_api(settings: CompositionSettings) -> K8sClientApi:
    """Build the production port from the in-cluster ServiceAccount credentials.

    ``settings`` is accepted for the composition root's call shape; the namespace and placement
    are read by the provider, not by this adapter. Raises ``ConfigException`` outside a cluster,
    which refuses the start instead of falling back to the fake.
    """

    from kubernetes import client, config

    configuration = client.Configuration()
    config.load_incluster_config(client_configuration=configuration)  # type: ignore[no-untyped-call]
    return K8sClientApi(client.CoreV1Api(client.ApiClient(configuration)))


def build_k8s_api_from_kubeconfig(context: str | None = None) -> K8sClientApi:
    """Build the port from a kubeconfig context. For the kind test suites only."""

    from kubernetes import client, config

    configuration = client.Configuration()
    config.load_kube_config(context=context, client_configuration=configuration)  # type: ignore[no-untyped-call]
    return K8sClientApi(client.CoreV1Api(client.ApiClient(configuration)))
