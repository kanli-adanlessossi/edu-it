"""Decision and classification types of the Kubernetes sandbox provider.

Value types only (kubernetes-deployment design, Data Models / Domain); the pure
functions that produce them arrive in later tasks. Standard library imports only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Literal

__all__ = [
    "CapacityDecision",
    "CniOutcome",
    "Fail",
    "RejectionKind",
    "Requeue",
    "RuntimeDecision",
]


class RejectionKind(Enum):
    """Classification of a pod create rejection by the Kubernetes API."""

    CAPACITY = "capacity"
    POD_SECURITY_VIOLATION = "pod-security-violation"
    RUNTIME_CLASS_MISSING = "runtime-class-missing"
    ALREADY_EXISTS = "already-exists"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Requeue:
    """Retry the capacity-rejected request after ``delay``."""

    delay: timedelta


@dataclass(frozen=True, slots=True)
class Fail:
    """Mark the capacity-rejected request failed."""

    reason: Literal["capacity"] = "capacity"


type CapacityDecision = Requeue | Fail


class RuntimeDecision(Enum):
    """Outcome of the runtime-isolation guard at startup."""

    OK = "ok"
    WARN = "warn"
    REFUSE = "refuse"


class CniOutcome(Enum):
    """Outcome of the NetworkPolicy-enforcing CNI check."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
