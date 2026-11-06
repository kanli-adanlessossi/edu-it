"""Scripted fake of the domain ``LabPodWatcher`` port."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from edu_it_domain.ports.lab_pods import LabPodEvent, LabPodWatcher

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

__all__ = ["Fake_LabPodWatcher"]


class Fake_LabPodWatcher(LabPodWatcher):
    """Replays scripted streams: each ``watch()`` call consumes the next script.

    A script is a list of events, or an exception instance that is raised when the
    stream is opened. An exhausted script list yields an empty stream.
    """

    def __init__(self, failed_pod_ids: Iterable[str] = ()) -> None:
        """Start with no scripted streams and the given failed pod ids."""
        self._scripts: deque[list[LabPodEvent] | Exception] = deque()
        self.failed: set[str] = set(failed_pod_ids)
        self.watch_calls = 0

    def script(self, stream: list[LabPodEvent] | Exception) -> None:
        """Queue one stream for the next ``watch()`` call."""
        self._scripts.append(stream)

    def watch(self) -> Iterator[LabPodEvent]:
        """Open the next scripted stream."""
        self.watch_calls += 1
        script = self._scripts.popleft() if self._scripts else []
        if isinstance(script, Exception):
            raise script
        return iter(script)

    def failed_pod_ids(self) -> frozenset[str]:
        """The currently failed or evicted pod ids."""
        return frozenset(self.failed)
