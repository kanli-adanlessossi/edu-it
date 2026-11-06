"""``KubernetesPodWatcher``: Lab_Pod loss detection (kubernetes-deployment Req 19.2, 19.5).

Implements the domain ``LabPodWatcher`` port on top of :class:`K8sApiPort`. The design allows
a polling fallback for the watch, and ``K8sApiPort`` has no watch call, so ``watch`` lists the
managed pods every ``poll_interval`` and yields a loss event when a pod is ``Failed`` (kind
``evicted`` when the status reason is ``Evicted``) or when a pod seen earlier has vanished.
The reaper tick remains the fallback for anything missed while the stream is down.

Raw API text is logged by the caller and never put in an event.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from edu_it_domain.ports.lab_pods import LabPodEvent, LabPodWatcher, PodLossKind
from edu_it_domain.ports.sandbox import SandboxHandle

from edu_it_infrastructure.sandbox.k8s_api import PHASE_FAILED, PodView
from edu_it_infrastructure.sandbox.k8s_provider import LABEL_MANAGED, LABEL_SESSION, PROVIDER_NAME

if TYPE_CHECKING:
    from collections.abc import Iterator

    from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

__all__ = ["POLL_INTERVAL", "KubernetesPodWatcher"]

_LOG = logging.getLogger(__name__)

#: Polling interval; keeps detection well inside the 30 s marking bound (Req 19.2).
POLL_INTERVAL: Final[timedelta] = timedelta(seconds=5)

_EVICTED_REASON: Final = "Evicted"


def _lost_kind(pod: PodView) -> PodLossKind | None:
    if pod.phase != PHASE_FAILED:
        return None
    return PodLossKind.EVICTED if pod.reason == _EVICTED_REASON else PodLossKind.FAILED


def _handle(pod: PodView) -> SandboxHandle:
    return SandboxHandle(pod.name, pod.labels.get(LABEL_SESSION, ""), PROVIDER_NAME)


class KubernetesPodWatcher(LabPodWatcher):
    """Poll-based loss detection of managed Lab_Pods in the Labs_Namespace."""

    def __init__(
        self,
        api: K8sApiPort,
        namespace: str,
        *,
        poll_interval: timedelta = POLL_INTERVAL,
        stop: threading.Event | None = None,
    ) -> None:
        """Bind the API, the namespace and an optional stop event ending ``watch``."""
        self._api = api
        self._namespace = namespace
        self._poll = poll_interval
        self._stop = stop if stop is not None else threading.Event()

    def _managed(self) -> list[PodView]:
        return list(self._api.list_pods(self._namespace, f"{LABEL_MANAGED}=1"))

    def failed_pod_ids(self) -> frozenset[str]:
        """Names of managed pods that are in phase ``Failed`` (includes evicted pods)."""
        return frozenset(p.name for p in self._managed() if _lost_kind(p) is not None)

    def watch(self) -> Iterator[LabPodEvent]:
        """Yield each loss once; ends only when the stop event is set, else raises on API errors."""
        seen: dict[str, PodView] = {}
        reported: set[str] = set()
        while not self._stop.is_set():
            current = {p.name: p for p in self._managed()}
            for name, pod in current.items():
                kind = _lost_kind(pod)
                if kind is not None and name not in reported:
                    reported.add(name)
                    yield LabPodEvent(_handle(pod), kind)
            for name, pod in seen.items():
                if name not in current and name not in reported and pod.labels.get(LABEL_SESSION):
                    reported.add(name)
                    yield LabPodEvent(_handle(pod), PodLossKind.DELETED)
            reported &= set(current) | set(seen)
            seen = current
            self._stop.wait(self._poll.total_seconds())
