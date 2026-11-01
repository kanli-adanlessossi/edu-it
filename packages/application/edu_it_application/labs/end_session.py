"""The ``EndSession`` use case (Req 6.4, 7.1, 15.7).

Learner-initiated end: compare-and-set the session to ``ended`` and enqueue
``lab.destroy``. The worker performs the actual Sandbox destruction. Ending a
session that is already terminal is a no-op that returns it (idempotent).
"""

from __future__ import annotations

from typing import Final

from edu_it_domain.labs.models import ACTIVE_SESSION_STATUSES, LabSession, SessionStatus, StableId
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.lab_sessions import LabSessionRepository
from edu_it_domain.ports.task_queue import TaskQueue

from edu_it_application.labs.errors import LabSessionNotFoundError

__all__ = ["DESTROY_JOB", "EndSessionUseCase", "destroy_dedupe_key"]

DESTROY_JOB: Final = "lab.destroy"


def destroy_dedupe_key(session_id: StableId) -> str:
    """Dedupe key of the destroy job of one session."""
    return f"destroy:{session_id}"


class EndSessionUseCase:
    """End a learner's own session and schedule its Sandbox destruction."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        queue: TaskQueue,
        clock: Clock,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._queue = queue
        self._clock = clock

    def execute(self, user_id: StableId, session_id: StableId) -> LabSession:
        """End ``session_id`` for ``user_id`` and return the stored session.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
        """
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)

        won = self._sessions.transition(
            session_id,
            expected=ACTIVE_SESSION_STATUSES,
            to=SessionStatus.ENDED,
            ended_at=self._clock.now(),
        )
        if won:
            # Dedupe-keyed so a retried end cannot pile up destroy jobs; destroy is idempotent.
            self._queue.enqueue(
                DESTROY_JOB,
                {"session_id": session_id},
                dedupe_key=destroy_dedupe_key(session_id),
            )
        # A loser was already terminal (ended, expired, failed): the worker owns its destroy.
        refreshed = self._sessions.get_for_owner(session_id, user_id)
        return refreshed if refreshed is not None else session
