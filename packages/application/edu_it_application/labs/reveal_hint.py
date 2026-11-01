"""The ``RevealLabHint`` use case (Req 14.2, 15.3).

Hints are progressive: level k is granted only after level k-1 was revealed.
Each newly granted level records exactly one ``hint_reveal`` (keyed by lab task).
Repeating an already revealed level records nothing and is granted again;
skipping ahead records nothing and is rejected. The hint text itself lives in
the content bundle and is served by the boundary once a level is granted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from edu_it_domain.ports.mastery import HintRevealRecord

from edu_it_application.labs.errors import (
    HintNotAvailableError,
    HintOutOfOrderError,
    LabNotFoundError,
    LabSessionNotFoundError,
    LabTaskNotFoundError,
)

if TYPE_CHECKING:
    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabCatalog
    from edu_it_domain.ports.lab_sessions import LabSessionRepository
    from edu_it_domain.ports.mastery import MasteryRepository

__all__ = ["HintGrant", "RevealLabHintUseCase"]


@dataclass(frozen=True, slots=True)
class HintGrant:
    """A granted hint level."""

    task_id: StableId
    level: int
    total_levels: int
    newly_revealed: bool
    """``False`` when the level had already been revealed (nothing recorded)."""


class RevealLabHintUseCase:
    """Grant the next hint level of a lab task."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        catalog: LabCatalog,
        mastery: MasteryRepository,
        clock: Clock,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._catalog = catalog
        self._mastery = mastery
        self._clock = clock

    def execute(
        self, user_id: StableId, session_id: StableId, task_id: StableId, level: int
    ) -> HintGrant:
        """Grant hint ``level`` (1-based) of ``task_id`` to ``user_id``.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
            LabNotFoundError: the session's lab is no longer in the catalog.
            LabTaskNotFoundError: the task is not part of the session's lab.
            HintNotAvailableError: ``level`` is below 1 or above the task's hint count.
            HintOutOfOrderError: ``level`` skips an unrevealed earlier level.
        """
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)
        lab = self._catalog.get(session.lab_id)
        if lab is None:
            raise LabNotFoundError(session.lab_id)
        task = next((t for t in lab.tasks if t.task_id == task_id), None)
        if task is None:
            raise LabTaskNotFoundError(task_id)
        if level < 1 or level > task.hint_count:
            raise HintNotAvailableError(task_id, level)

        revealed = sum(
            1 for r in self._mastery.list_hint_reveals(user_id) if r.lab_task_id == task_id
        )
        if level <= revealed:
            return HintGrant(task_id, level, task.hint_count, newly_revealed=False)
        if level != revealed + 1:
            raise HintOutOfOrderError(task_id, level, revealed + 1)

        self._mastery.add_hint_reveal(
            HintRevealRecord(
                user_id=user_id,
                question_id=None,
                revealed_at=self._clock.now(),
                lab_task_id=task_id,
            )
        )
        return HintGrant(task_id, level, task.hint_count, newly_revealed=True)
