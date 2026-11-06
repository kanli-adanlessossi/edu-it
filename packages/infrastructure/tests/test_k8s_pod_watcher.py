"""Unit tests for ``KubernetesPodWatcher`` (kubernetes-deployment task 8.7).

# Claims: 19.2, 19.5
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from edu_it_domain.ports.lab_pods import LabPodWatcher, PodLossKind
from edu_it_infrastructure.sandbox.k8s_api import PodView
from edu_it_infrastructure.sandbox.k8s_pod_watcher import KubernetesPodWatcher
from edu_it_infrastructure.sandbox.k8s_provider import LABEL_MANAGED, LABEL_SESSION

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _pod(name: str, session: str, phase: str = "Running", reason: str = "") -> PodView:
    return PodView(
        name=name,
        namespace="labs",
        labels={LABEL_MANAGED: "1", LABEL_SESSION: session},
        annotations={},
        phase=phase,
        ready=phase == "Running",
        unschedulable=False,
        created_at=_T0,
        deletion_timestamp=None,
        reason=reason,
    )


class _Api:
    """Serves one scripted pod listing per ``list_pods`` call; the last one repeats."""

    def __init__(self, *listings: list[PodView]) -> None:
        self._listings = list(listings)
        self.calls = 0

    def list_pods(self, namespace: str, label_selector: str = "") -> list[PodView]:
        assert label_selector == f"{LABEL_MANAGED}=1"
        index = min(self.calls, len(self._listings) - 1)
        self.calls += 1
        return self._listings[index]


def test_failed_pod_ids_lists_failed_and_evicted_pods_only() -> None:
    api = _Api([_pod("a", "s1"), _pod("b", "s2", "Failed", "Evicted"), _pod("c", "s3", "Failed")])
    watcher = KubernetesPodWatcher(api, "labs")  # type: ignore[arg-type]
    assert isinstance(watcher, LabPodWatcher)
    assert watcher.failed_pod_ids() == frozenset({"b", "c"})


def test_watch_reports_each_loss_once_and_a_vanished_pod_as_deleted() -> None:
    stop = threading.Event()
    api = _Api(
        [_pod("a", "s1"), _pod("b", "s2"), _pod("c", "s3")],
        [_pod("a", "s1"), _pod("b", "s2", "Failed", "Evicted"), _pod("c", "s3")],
        [_pod("a", "s1"), _pod("b", "s2", "Failed", "Evicted")],  # c vanished
        [_pod("a", "s1"), _pod("b", "s2", "Failed", "Evicted")],  # nothing new
    )
    watcher = KubernetesPodWatcher(api, "labs", poll_interval=timedelta(0), stop=stop)  # type: ignore[arg-type]

    events = []
    for event in watcher.watch():
        events.append((event.handle.sandbox_id, event.handle.session_stable_id, event.kind))
        if len(events) == 2:
            stop.set()

    assert events == [
        ("b", "s2", PodLossKind.EVICTED),
        ("c", "s3", PodLossKind.DELETED),
    ]


def test_watch_marks_a_non_evicted_failed_pod_as_failed() -> None:
    stop = threading.Event()
    api = _Api([_pod("a", "s1", "Failed", "NodeLost")])
    watcher = KubernetesPodWatcher(api, "labs", poll_interval=timedelta(0), stop=stop)  # type: ignore[arg-type]
    event = next(iter(watcher.watch()))
    stop.set()
    assert event.kind is PodLossKind.FAILED
