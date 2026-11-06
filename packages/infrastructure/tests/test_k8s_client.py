"""Unit tests of the production ``K8sApiPort`` client adapter (kubernetes-deployment task 7).

They need the ``kubernetes`` package but no cluster: the translation of API failures, the exit
status parsing and the pod view are pure, and ``K8sClientApi`` is driven over a stub ``CoreV1Api``.
The adapter against a real API server is covered by ``tests/k8s/test_provider_contract_kind.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from edu_it_infrastructure.sandbox.k8s_api import (
    ExecOutput,
    K8sApiError,
    K8sApiPort,
    K8sStreamClosed,
)
from edu_it_infrastructure.sandbox.k8s_client import (
    DELETE_GRACE_SECONDS,
    K8sClientApi,
    exit_code_of,
    pod_view_from_v1pod,
    translate_exception,
)
from edu_it_infrastructure.sandbox.k8s_errors import (
    CreateRejection,
    classify_create_rejection,
    map_api_exception,
)
from hypothesis import given
from hypothesis import strategies as st
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import MaxRetryError, ReadTimeoutError


def _api_exception(status: int, reason: str, message: str, phrase: str) -> ApiException:
    body = json.dumps({"kind": "Status", "status": "Failure", "reason": reason, "message": message})
    return ApiException(status=status, reason=phrase, body=body)


# Claims: 1.5, 2.11, 2.12, 22.1
@pytest.mark.parametrize(
    ("status", "reason", "message", "phrase", "expected"),
    [
        (403, "Forbidden", "pods \"p\" is forbidden: exceeded quota: q", "Forbidden",
         CreateRejection.CAPACITY),
        (403, "Forbidden", 'pods "p" is forbidden: violates PodSecurity "restricted"', "Forbidden",
         CreateRejection.POD_SECURITY_VIOLATION),
        (403, "Forbidden", 'pods "p" is forbidden: pod rejected: RuntimeClass "x" not found',
         "Forbidden", CreateRejection.RUNTIME_CLASS_MISSING),
        (409, "AlreadyExists", 'pods "p" already exists', "Conflict",
         CreateRejection.ALREADY_EXISTS),
    ],
)  # fmt: skip
def test_api_exception_keeps_the_status_object_reason(
    status: int, reason: str, message: str, phrase: str, expected: CreateRejection
) -> None:
    error = translate_exception(_api_exception(status, reason, message, phrase))
    assert isinstance(error, K8sApiError)
    assert (error.status, error.reason, error.message) == (status, reason, message)
    # The HTTP phrase ("Conflict") is not the Status reason ("AlreadyExists") the classifier reads.
    assert classify_create_rejection(error.status, error.reason, error.message) is expected


# Claims: 1.11, 1.12
def test_not_found_maps_to_sandbox_gone() -> None:
    error = translate_exception(_api_exception(404, "NotFound", 'pods "p" not found', "Not Found"))
    assert isinstance(error, K8sApiError)
    assert type(map_api_exception(error)).__name__ == "SandboxGone"


# Claims: 1.11, 1.12
def test_unreachable_server_becomes_builtin_connection_error() -> None:
    refused = translate_exception(MaxRetryError(None, "/api/v1/pods", reason=None))  # type: ignore[arg-type]
    assert isinstance(refused, ConnectionError)
    timed_out = translate_exception(ReadTimeoutError(None, "/api/v1/pods", "read timed out"))  # type: ignore[arg-type]
    assert isinstance(timed_out, TimeoutError)
    assert type(map_api_exception(refused)).__name__ == "ProviderUnavailable"


# Claims: 1.11, 1.12
@pytest.mark.parametrize(
    ("handshake", "status", "reason"),
    [
        ('Handshake status 404 Not Found -+-+- {} -+-+- b\'{"reason":"NotFound","message":"x"}\'',
         404, "NotFound"),
        ("Handshake status 403 Forbidden -+-+- {} -+-+- b'{\"message\":\"no exec\"}'",
         403, "Forbidden"),
        ("Handshake status 500 Internal Server Error -+-+- {} -+-+- b''", 500, "InternalError"),
    ],
)  # fmt: skip
def test_failed_websocket_upgrade_is_read_from_the_handshake_text(
    handshake: str, status: int, reason: str
) -> None:
    error = translate_exception(ApiException(status=0, reason=handshake))
    assert isinstance(error, K8sApiError)
    assert (error.status, error.reason) == (status, reason)


# Claims: 1.11
def test_websocket_connect_failure_without_a_handshake_is_a_connection_error() -> None:
    error = translate_exception(ApiException(status=0, reason="[Errno 111] Connection refused"))
    assert isinstance(error, ConnectionError)


# Claims: 1.10, 5.6
def test_exit_status_parsing() -> None:
    ok = json.dumps({"status": "Success"})
    assert exit_code_of(ok.encode()) == 0
    failed = json.dumps(
        {
            "status": "Failure",
            "reason": "NonZeroExitCode",
            "details": {"causes": [{"reason": "ExitCode", "message": "137"}]},
        }
    )
    assert exit_code_of(failed) == 137
    with pytest.raises(K8sStreamClosed):
        exit_code_of(b"")  # no Status: the stream died, never "exit 0"
    start_failure = json.dumps(
        {"status": "Failure", "message": "failed to start exec: fork/exec: resource unavailable"}
    )
    with pytest.raises(K8sApiError) as caught:
        exit_code_of(start_failure)
    assert caught.value.status == 500


# Claims: 1.10
@given(code=st.integers(min_value=1, max_value=255))
def test_exit_code_round_trips(code: int) -> None:
    status = {
        "status": "Failure",
        "reason": "NonZeroExitCode",
        "details": {"causes": [{"reason": "ExitCode", "message": str(code)}]},
    }
    assert exit_code_of(json.dumps(status)) == code


def _v1pod(**over: Any) -> Any:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    condition = SimpleNamespace
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="lab-1",
            namespace="labs",
            labels={"edu-it.managed": "1"},
            annotations=None,
            creation_timestamp=now,
            deletion_timestamp=None,
        ),
        spec=SimpleNamespace(node_name=None, containers=[SimpleNamespace(name="learner")]),
        status=SimpleNamespace(
            phase="Pending",
            reason=None,
            message=None,
            conditions=[
                condition(
                    type="PodScheduled",
                    status="False",
                    reason="Unschedulable",
                    message="0/3 nodes are available",
                )
            ],
        ),
    )
    for key, value in over.items():
        setattr(pod, key, value)
    return pod


# Claims: 1.5, 22.8
def test_pending_unschedulable_pod_view() -> None:
    view = pod_view_from_v1pod(_v1pod())
    assert (view.phase, view.unschedulable, view.reason, view.ready) == (
        "Pending",
        True,
        "Unschedulable",
        False,
    )
    assert view.message == "0/3 nodes are available"
    assert view.container_names == ("learner",)
    assert dict(view.annotations) == {}


class _Core:
    """Stub ``CoreV1Api`` that records the calls of the adapter."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.error: Exception | None = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        if self.error is not None:
            raise self.error
        return _v1pod()

    def create_namespaced_pod(self, *args: Any, **kwargs: Any) -> Any:
        return self._record("create", *args, **kwargs)

    def delete_namespaced_pod(self, *args: Any, **kwargs: Any) -> Any:
        return self._record("delete", *args, **kwargs)

    def patch_namespaced_pod(self, *args: Any, **kwargs: Any) -> Any:
        return self._record("patch", *args, **kwargs)

    def list_namespaced_pod(self, *args: Any, **kwargs: Any) -> Any:
        self._record("list", *args, **kwargs)
        return SimpleNamespace(items=[_v1pod(), _v1pod()])


# Claims: 6.3, 6.4
def test_delete_keeps_the_pod_visible_until_the_kubelet_stops_it() -> None:
    core = _Core()
    K8sClientApi(core).delete_pod("labs", "lab-1")
    ((_, _, kwargs),) = core.calls
    assert kwargs["body"].grace_period_seconds == DELETE_GRACE_SECONDS == 1


# Claims: 6.5
def test_label_is_a_merge_patch_of_labels_only() -> None:
    core = _Core()
    K8sClientApi(core).label_pod("labs", "lab-1", {"edu-it.io/destroy": "pending"})
    ((_, args, _),) = core.calls
    assert args == ("lab-1", "labs", {"metadata": {"labels": {"edu-it.io/destroy": "pending"}}})


# Claims: 1.7
def test_list_passes_the_selector_and_sorts_by_name() -> None:
    core = _Core()
    pods = K8sClientApi(core).list_pods("labs", "edu-it.managed=1")
    assert core.calls[0][2]["label_selector"] == "edu-it.managed=1"
    assert [p.name for p in pods] == sorted(p.name for p in pods)


# Claims: 1.5, 2.11
def test_adapter_raises_the_port_exception_not_the_client_one() -> None:
    core = _Core()
    core.error = _api_exception(409, "AlreadyExists", 'pods "p" already exists', "Conflict")
    with pytest.raises(K8sApiError) as caught:
        K8sClientApi(core).create_pod("labs", {"kind": "Pod"})
    assert (caught.value.status, caught.value.reason) == (409, "AlreadyExists")


# Claims: 1.1
def test_adapter_satisfies_the_port() -> None:
    assert isinstance(K8sClientApi(_Core()), K8sApiPort)
    assert ExecOutput(0).timed_out is False
