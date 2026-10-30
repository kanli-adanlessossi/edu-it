"""The ``GetSession`` and ``ListSessions`` read use cases (Req 17.4, 17.5).

A session owned by someone else is indistinguishable from a missing one.
Lists page by keyset ``(created_at, stable_id)``, never by offset.
"""

from __future__ import annotations

from dataclasses import dataclass

from edu_it_domain.labs.models import LabSession, StableId
from edu_it_domain.ports.lab_sessions import LabSessionRepository
from edu_it_domain.ports.pagination import Keyset

from edu_it_application.labs.errors import LabSessionNotFoundError

__all__ = ["GetSessionUseCase", "ListSessionsUseCase", "SessionPage"]


class GetSessionUseCase:
    """Read one of the learner's own sessions."""

    def __init__(self, *, sessions: LabSessionRepository) -> None:
        """Wire the repository."""
        self._sessions = sessions

    def execute(self, user_id: StableId, session_id: StableId) -> LabSession:
        """Return the session.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
        """
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)
        return session


@dataclass(frozen=True, slots=True)
class SessionPage:
    """One keyset page of sessions."""

    items: tuple[LabSession, ...]
    next_after: Keyset | None
    """Position to pass as ``after`` for the next page; ``None`` on the last page."""


class ListSessionsUseCase:
    """List the learner's sessions, oldest first."""

    def __init__(self, *, sessions: LabSessionRepository) -> None:
        """Wire the repository."""
        self._sessions = sessions

    def execute(self, user_id: StableId, *, limit: int, after: Keyset | None = None) -> SessionPage:
        """Return up to ``limit`` sessions strictly after ``after``."""
        if limit < 1:
            raise ValueError("limit must be positive")
        # Fetch one extra row to know whether another page exists.
        rows = self._sessions.list_for_user_page(user_id, after=after, limit=limit + 1)
        items = rows[:limit]
        next_after = (items[-1].created_at, items[-1].stable_id) if len(rows) > limit else None
        return SessionPage(items=items, next_after=next_after)
