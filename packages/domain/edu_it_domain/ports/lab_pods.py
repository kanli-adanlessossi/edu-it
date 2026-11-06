"""The ``LabPodWatcher`` port (kubernetes-deployment Req 19.2, 19.5, 6.7).

Reports the loss of Lab_Pods (eviction, node loss, failure, deletion) to the Worker. The
Kubernetes implementation watches managed pods; the fake is scripted. This module imports
only the standard library and the sandbox port types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from edu_it_domain.ports.sandbox import SandboxHandle

__all__ = ["LabPodEvent", "LabPodWatcher", "PodLossKind"]


class PodLossKind(Enum):
    """Why a Lab_Pod was lost."""

    EVICTED = "evicted"
    """Evicted by the kubelet; the pod object remains and must be removed."""

    FAILED = "failed"
    """Phase ``Failed`` (``NodeLost``, ``Shutdown``, ``Terminated``...); the object remains."""

    DELETED = "deleted"
    """The pod object disappeared without a Worker request."""


@dataclass(frozen=True, slots=True)
class LabPodEvent:
    """One detected loss of a Lab_Pod."""

    handle: SandboxHandle
    kind: PodLossKind


@runtime_checkable
class LabPodWatcher(Protocol):
    """Port for observing the loss of managed Lab_Pods."""

    def watch(self) -> Iterator[LabPodEvent]:
        """Yield loss events until the stream ends.

        Blocks between events. A normal end or any exception means the stream broke and
        the caller reconnects with backoff.
        """
        ...

    def failed_pod_ids(self) -> frozenset[str]:
        """Ids of managed pods that are failed or evicted right now (reaper input)."""
        ...
