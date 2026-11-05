"""Unit tests for the Kubernetes rejection classifier and error mapper."""

from __future__ import annotations

import pytest
from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    ProvisionCapacityError,
    ProvisionError,
    SandboxError,
    SandboxGone,
)
from edu_it_infrastructure.sandbox.k8s_errors import (
    CreateRejection,
    classify_create_rejection,
    map_api_exception,
)


class _ApiException(Exception):
    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(body)
        self.status = status


class MaxRetryError(Exception):
    pass


class WebSocketConnectionClosedException(Exception):  # noqa: N818
    pass


# Claims: 22.1
def test_capacity_error_is_a_provision_error() -> None:
    assert issubclass(ProvisionCapacityError, ProvisionError)


@pytest.mark.parametrize(
    ("status", "reason", "message", "expected"),
    [
        (
            403,
            "Forbidden",
            "pods is forbidden: exceeded quota: lab-quota",
            CreateRejection.CAPACITY,
        ),
        (
            403,
            "Forbidden",
            'violates PodSecurity "restricted:latest"',
            CreateRejection.POD_SECURITY_VIOLATION,
        ),
        (
            403,
            "Forbidden",
            'RuntimeClass.node.k8s.io "gvisor" not found',
            CreateRejection.RUNTIME_CLASS_MISSING,
        ),
        (
            422,
            "Invalid",
            'pod rejected: RuntimeClass "kata" not found',
            CreateRejection.RUNTIME_CLASS_MISSING,
        ),
        (409, "AlreadyExists", "pods already exists", CreateRejection.ALREADY_EXISTS),
        (500, "InternalError", "boom", CreateRejection.OTHER),
        (422, "Invalid", "exceeded quota", CreateRejection.OTHER),
        (None, None, None, CreateRejection.OTHER),
    ],
)
# Claims: 2.11, 2.12, 3.8, 22.1
def test_classify_create_rejection_table(
    status: int | None, reason: str | None, message: str | None, expected: CreateRejection
) -> None:
    assert classify_create_rejection(status, reason, message) is expected


_REAL_API_MESSAGES = [
    pytest.param(
        403,
        "Forbidden",
        'pods "lab-0a1b2c3d4e5f60718293" is forbidden: exceeded quota: lab-quota, '
        "requested: limits.memory=512Mi, used: limits.memory=15872Mi, limited: limits.memory=16Gi",
        CreateRejection.CAPACITY,
        id="quota-exceeded",
    ),
    pytest.param(
        403,
        "Forbidden",
        'pods "lab-0a1b2c3d4e5f60718293" is forbidden: violates PodSecurity "restricted:latest": '
        'allowPrivilegeEscalation != false (container "learner" must set '
        "securityContext.allowPrivilegeEscalation=false), unrestricted capabilities "
        '(container "learner" must set securityContext.capabilities.drop=["ALL"])',
        CreateRejection.POD_SECURITY_VIOLATION,
        id="pod-security-violation",
    ),
    pytest.param(
        403,
        "Forbidden",
        'pods "lab-0a1b2c3d4e5f60718293" is forbidden: pod rejected: '
        'RuntimeClass "gvisor" not found',
        CreateRejection.RUNTIME_CLASS_MISSING,
        id="runtime-class-not-found",
    ),
    pytest.param(
        409,
        "AlreadyExists",
        'pods "lab-0a1b2c3d4e5f60718293" already exists',
        CreateRejection.ALREADY_EXISTS,
        id="already-exists",
    ),
]


@pytest.mark.parametrize(("status", "reason", "message", "expected"), _REAL_API_MESSAGES)
# Claims: 2.11, 2.12
def test_classifier_on_real_api_server_messages(
    status: int, reason: str, message: str, expected: CreateRejection
) -> None:
    assert classify_create_rejection(status, reason, message) is expected


# Claims: 2.11, 2.12
def test_real_messages_do_not_cross_classify() -> None:
    rows = [p.values for p in _REAL_API_MESSAGES]
    kinds = [classify_create_rejection(s, r, m) for s, r, m, _ in rows]
    assert len(set(kinds)) == len(kinds)


# Claims: 1.11, 1.12
def test_404_and_closed_stream_map_to_gone() -> None:
    assert isinstance(map_api_exception(_ApiException(404)), SandboxGone)
    assert isinstance(map_api_exception(WebSocketConnectionClosedException()), SandboxGone)


# Claims: 1.11, 1.12
def test_connection_failure_maps_to_provider_unavailable() -> None:
    assert isinstance(map_api_exception(MaxRetryError()), ProviderUnavailable)
    assert isinstance(map_api_exception(ConnectionRefusedError()), ProviderUnavailable)


# Claims: 1.11
def test_raw_api_text_is_not_in_the_message() -> None:
    secret = "token=abc internal-host.local"
    for exc in (_ApiException(404, secret), MaxRetryError(secret), _ApiException(500, secret)):
        mapped = map_api_exception(exc)
        assert isinstance(mapped, SandboxError)
        assert secret not in str(mapped)
