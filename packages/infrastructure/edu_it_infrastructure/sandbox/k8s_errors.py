"""Pure classifiers for Kubernetes API failures (kubernetes-deployment Req 1.11, 1.12,
2.11, 2.12, 3.8, 22.1).

No Kubernetes client import: functions work on plain values and duck-typed
exceptions, so they are testable without a cluster. Raw API text is never put
in the returned exception messages; the provider logs the detail itself.
"""

from __future__ import annotations

import enum

from edu_it_domain.ports.sandbox import (
    ProviderUnavailable,
    SandboxError,
    SandboxGone,
)

__all__ = ["CreateRejection", "classify_create_rejection", "map_api_exception"]


class CreateRejection(enum.Enum):
    """Why the API server rejected a pod create."""

    CAPACITY = "Capacity"
    POD_SECURITY_VIOLATION = "PodSecurityViolation"
    RUNTIME_CLASS_MISSING = "RuntimeClassMissing"
    ALREADY_EXISTS = "AlreadyExists"
    OTHER = "Other"


def classify_create_rejection(
    status: int | None, reason: str | None, message: str | None
) -> CreateRejection:
    """Classify a pod-create rejection per the design table.

    Only status 403 with an exceeded-quota message yields ``CAPACITY``.
    """
    text = (message or "").lower()
    if status == 403 and "exceeded quota" in text:
        return CreateRejection.CAPACITY
    if status == 403 and "violates podsecurity" in text:
        return CreateRejection.POD_SECURITY_VIOLATION
    if status in (403, 422) and "runtimeclass" in text and "not found" in text:
        return CreateRejection.RUNTIME_CLASS_MISSING
    if status == 409 and reason == "AlreadyExists":
        return CreateRejection.ALREADY_EXISTS
    return CreateRejection.OTHER


_CLOSED_STREAM_NAMES = frozenset(
    {
        "WebSocketConnectionClosedException",
        "BrokenPipeError",
        "ConnectionResetError",
        "K8sStreamClosed",
    }
)
_CONNECTION_NAMES = frozenset(
    {
        "MaxRetryError",
        "NewConnectionError",
        "ProtocolError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "ConnectionError",
        "ConnectionRefusedError",
        "TimeoutError",
        "SSLError",
    }
)


def _type_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def map_api_exception(exc: BaseException) -> SandboxError:
    """Map a Kubernetes client failure to a sandbox error with a fixed message.

    404 or a closed stream becomes ``SandboxGone``; a connection failure to
    the API server becomes ``ProviderUnavailable``; anything else becomes a
    generic ``SandboxError``.
    """
    if isinstance(exc, SandboxError):
        return exc
    names = _type_names(exc)
    if getattr(exc, "status", None) == 404 or names & _CLOSED_STREAM_NAMES:
        return SandboxGone("sandbox is gone")
    if names & _CONNECTION_NAMES or isinstance(exc, ConnectionError | TimeoutError):
        return ProviderUnavailable("sandbox provider is unavailable")
    return SandboxError("sandbox operation failed")
