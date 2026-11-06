"""The ``watch_lab_pods`` job logic (kubernetes-deployment Req 19.2, 19.5, 19.8).

Consumes Lab_Pod loss events from the ``LabPodWatcher`` port. For each event it ends the
session by infrastructure loss and removes the evicted or failed pod object. When the
stream ends or raises, it reconnects with the ``restart`` backoff schedule (the last
delay repeats), and the schedule restarts after a stream that delivered events. The
reaper tick is the fallback for anything missed while disconnected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from edu_it_domain.labs.k8s_rules import reconnect_delays
from edu_it_domain.ports.lab_pods import PodLossKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_domain.ports.lab_metrics import LabMetrics
    from edu_it_domain.ports.lab_pods import LabPodEvent, LabPodWatcher
    from edu_it_domain.ports.sandbox import SandboxProvider

    from edu_it_application.labs.end_by_infrastructure import EndByInfrastructure

__all__ = ["WatchLabPods"]

_LOG = logging.getLogger(__name__)


class WatchLabPods:
    """Turn pod loss events into ended sessions and removed pod objects."""

    def __init__(
        self,
        *,
        watcher: LabPodWatcher,
        end: EndByInfrastructure,
        sandbox: SandboxProvider,
        metrics: LabMetrics,
        sleep: Callable[[float], None],
    ) -> None:
        """Wire the ports; ``sleep`` is injected so tests never wait."""
        self._watcher = watcher
        self._end = end
        self._sandbox = sandbox
        self._metrics = metrics
        self._sleep = sleep

    def handle(self, event: LabPodEvent) -> None:
        """Process one loss event. Never raises: the reaper retries what fails here."""
        try:
            self._end.execute(event.handle.session_stable_id)
        except Exception:
            _LOG.warning(
                "ending session %s by infrastructure failed",
                event.handle.session_stable_id,
                exc_info=True,
            )
        if event.kind is PodLossKind.DELETED:
            return  # the object is already gone
        try:
            self._sandbox.destroy(event.handle)
        except Exception:
            _LOG.warning("removing lost pod %s failed", event.handle.sandbox_id, exc_info=True)
            return
        if event.kind is PodLossKind.EVICTED:
            self._metrics.inc_reaper_deletion(reason="evicted")

    def run(self, should_stop: Callable[[], bool]) -> None:
        """Watch until ``should_stop()`` is true, reconnecting with backoff."""
        delays = reconnect_delays("restart")
        failures = 0
        while not should_stop():
            delivered = False
            try:
                for event in self._watcher.watch():
                    delivered = True
                    self.handle(event)
                    if should_stop():
                        return
            except Exception:
                _LOG.warning("lab pod watch broke; reconnecting", exc_info=True)
            if should_stop():
                return
            failures = 0 if delivered else failures + 1
            self._sleep(delays[min(max(failures - 1, 0), len(delays) - 1)].total_seconds())
