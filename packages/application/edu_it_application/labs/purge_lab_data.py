"""The ``PurgeLabData`` retention job logic (Req 9.1 to 9.4).

Deletes ended lab sessions, their runs and task completions once they ended more
than ``LAB_RETENTION_PERIOD`` ago. The repository deletes the three tables in one
transaction. Mastery and ``mastery_event`` rows are never touched: this use case
has no access to their repositories. The job is idempotent: a second run at the
same Clock instant finds nothing left to delete.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from edu_it_domain import config

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_sessions import LabSessionRepository

__all__ = ["PurgeLabData"]


class PurgeLabData:
    """Delete lab data of sessions that ended before the retention cutoff."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        clock: Clock,
        retention: timedelta = config.LAB_RETENTION_PERIOD,
    ) -> None:
        """Wire the session repository (which owns runs and completions) and the clock."""
        self._sessions = sessions
        self._clock = clock
        self._retention = retention

    def execute(self) -> int:
        """Purge expired lab data and return the number of sessions deleted."""
        cutoff = self._clock.now() - self._retention
        # Only terminal sessions with ended_at < cutoff qualify (Req 9.2).
        ids = [s.stable_id for s in self._sessions.list_ended_before(cutoff)]
        if not ids:
            return 0
        return self._sessions.delete_session_data(ids)
