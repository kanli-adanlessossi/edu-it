"""Lab session and grading run repository ports (hands-on-labs Req 7.1, 7.4, 17.5).

Every status change is a compare-and-set on ``(id, expected status)`` so
concurrent workers and requests cannot both win. List reads page by keyset
``(sort key, stable_id)``, never by offset (Req 17.5), reusing
:data:`~edu_it_domain.ports.pagination.Keyset`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime, timedelta

    from edu_it_domain.labs.models import (
        CheckResult,
        GradingRun,
        LabSession,
        RunStatus,
        SessionStatus,
        StableId,
        TaskCompletion,
    )
    from edu_it_domain.ports.pagination import Keyset

__all__ = [
    "ActiveSessionExists",
    "GradingRunRepository",
    "LabCompletionReader",
    "LabSessionRepository",
]


@runtime_checkable
class LabCompletionReader(Protocol):
    """Read port for a learner's recorded lab task completions (labs Req 19.7)."""

    def list_task_completions(self, user_id: StableId) -> Sequence[TaskCompletion]:
        """All of the learner's task completions, ordered by completion then task id."""
        ...


class ActiveSessionExists(Exception):
    """The learner already has an Active_Session (Req 7.1)."""


@runtime_checkable
class LabSessionRepository(Protocol):
    """Port for ``lab_session`` rows."""

    def add_if_no_active(self, session: LabSession) -> None:
        """Insert ``session`` unless its learner has an Active_Session.

        Atomic even under concurrent starts. Raises :class:`ActiveSessionExists`.
        """
        ...

    def get(self, session_id: StableId) -> LabSession | None:
        """Return the session, or ``None`` when absent."""
        ...

    def get_for_owner(self, session_id: StableId, user_id: StableId) -> LabSession | None:
        """Return the session only if ``user_id`` owns it; ``None`` if absent or foreign."""
        ...

    def get_active_for_user(self, user_id: StableId) -> LabSession | None:
        """Return the learner's Active_Session, if any."""
        ...

    def transition(
        self,
        session_id: StableId,
        *,
        expected: Collection[SessionStatus],
        to: SessionStatus,
        **fields: object,
    ) -> bool:
        """Compare-and-set the status from any of ``expected`` to ``to``.

        ``fields`` are further columns set in the same statement. Returns
        ``True`` only for the winner; a loser changes nothing.
        """
        ...

    def try_accept_grading(
        self, session_id: StableId, *, now: datetime, min_interval: timedelta
    ) -> bool:
        """CAS ``ready`` to ``grading`` and stamp ``last_grading_accepted_at``.

        Succeeds only when the session is ``ready`` and the previous accepted
        request is more than ``min_interval`` before ``now``.
        """
        ...

    def claim_provision_attempt(
        self, session_id: StableId, *, expected_attempts: int, now: datetime
    ) -> bool:
        """CAS on ``provision_attempts`` while ``provisioning``; increments it.

        A duplicate delivery sees a different count and loses.
        """
        ...

    def touch_activity(self, session_id: StableId, at: datetime) -> None:
        """Record learner activity at ``at`` (never moves backwards)."""
        ...

    def bump_terminal_epoch(self, session_id: StableId) -> int | None:
        """Atomically increment ``terminal_epoch`` and return the new value.

        ``None`` when the session is absent. The epoch fences stale terminal
        connections across API replicas (Req 12.10).
        """
        ...

    def count_started_since(self, user_id: StableId, since: datetime) -> int:
        """Count the learner's sessions created at or after ``since``."""
        ...

    def list_for_user_page(
        self, user_id: StableId, *, after: Keyset | None = None, limit: int
    ) -> tuple[LabSession, ...]:
        """Up to ``limit`` sessions after ``after``, ordered ``(created_at, stable_id)``."""
        ...

    def list_active(self) -> Sequence[LabSession]:
        """All Active_Sessions (reaper)."""
        ...

    def list_ended_before(self, cutoff: datetime) -> Sequence[LabSession]:
        """Terminal sessions that ended before ``cutoff`` (retention)."""
        ...

    def delete_session_data(self, session_ids: Collection[StableId]) -> int:
        """Delete the sessions with their runs and completions; return sessions deleted."""
        ...


@runtime_checkable
class GradingRunRepository(Protocol):
    """Port for ``lab_run`` rows and their results and completions."""

    def add(self, run: GradingRun) -> None:
        """Insert a new run."""
        ...

    def get(self, run_id: StableId) -> GradingRun | None:
        """Return the run, or ``None`` when absent."""
        ...

    def append_result(self, run_id: StableId, result: CheckResult) -> None:
        """Append a Check result; ordered and idempotent per check id."""
        ...

    def finish(self, run_id: StableId, *, status: RunStatus, graded_at: datetime) -> bool:
        """CAS ``running`` to ``status``; ``True`` only for the winner."""
        ...

    def list_running(self) -> Sequence[GradingRun]:
        """All runs still ``running`` (reaper)."""
        ...

    def first_pass_exists(self, user_id: StableId, task_id: StableId) -> bool:
        """Whether the learner already completed the task."""
        ...

    def add_task_completions(self, run_id: StableId, rows: Sequence[TaskCompletion]) -> None:
        """Insert completions; idempotent per ``(user, task)``."""
        ...
