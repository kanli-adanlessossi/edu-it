"""In-memory lab repositories (hands-on-labs Req 1.2, 25.7).

They honor the port semantics without a database: every status change is a
compare-and-set under a lock, and ``add_if_no_active`` enforces one
Active_Session per learner atomically.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from edu_it_domain.labs.models import RunStatus, SessionStatus
from edu_it_domain.ports.lab_sessions import ActiveSessionExists

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime, timedelta

    from edu_it_domain.labs.models import (
        CheckResult,
        GradingRun,
        LabSession,
        StableId,
        TaskCompletion,
    )
    from edu_it_domain.ports.pagination import Keyset

__all__ = ["In_Memory_GradingRunRepository", "In_Memory_LabSessionRepository"]

_TERMINAL = frozenset(
    {
        SessionStatus.ENDED,
        SessionStatus.EXPIRED,
        SessionStatus.IDLE_EXPIRED,
        SessionStatus.FAILED,
        SessionStatus.ENDED_BY_INFRASTRUCTURE,
    }
)


class In_Memory_GradingRunRepository:
    """In-memory ``GradingRunRepository``."""

    def __init__(self) -> None:
        self._runs: dict[StableId, GradingRun] = {}
        self._completions: dict[tuple[StableId, StableId, StableId], TaskCompletion] = {}
        self._lock = threading.RLock()

    def add(self, run: GradingRun) -> None:
        with self._lock:
            if run.stable_id in self._runs:
                raise ValueError(f"run exists: {run.stable_id}")
            self._runs[run.stable_id] = run

    def get(self, run_id: StableId) -> GradingRun | None:
        return self._runs.get(run_id)

    def append_result(self, run_id: StableId, result: CheckResult) -> None:
        with self._lock:
            run = self._runs[run_id]
            if any(r.check_id == result.check_id for r in run.results):
                return
            self._runs[run_id] = replace(run, results=(*run.results, result))

    def finish(self, run_id: StableId, *, status: RunStatus, graded_at: datetime) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status is not RunStatus.RUNNING:
                return False
            self._runs[run_id] = replace(run, status=status, graded_at=graded_at)
            return True

    def list_running(self) -> Sequence[GradingRun]:
        return tuple(r for r in self._runs.values() if r.status is RunStatus.RUNNING)

    def first_pass_exists(self, user_id: StableId, task_id: StableId) -> bool:
        with self._lock:
            return any(u == user_id and t == task_id for (u, t, _r) in self._completions)

    def add_task_completions(self, run_id: StableId, rows: Sequence[TaskCompletion]) -> None:
        with self._lock:
            for row in rows:
                self._completions.setdefault((row.user_id, row.task_id, run_id), row)

    def list_task_completions(self, user_id: StableId) -> Sequence[TaskCompletion]:
        with self._lock:
            rows = [c for c in self._completions.values() if c.user_id == user_id]
        return tuple(sorted(rows, key=lambda c: (c.completed_at, c.task_id)))

    # -- helpers used by the session repository -----------------------------

    def delete_for_sessions(self, session_ids: Collection[StableId]) -> None:
        with self._lock:
            doomed = {r.stable_id for r in self._runs.values() if r.session_id in session_ids}
            for run_id in doomed:
                del self._runs[run_id]
            for key in [k for k, c in self._completions.items() if c.run_id in doomed]:
                del self._completions[key]


class In_Memory_LabSessionRepository:
    """In-memory ``LabSessionRepository``."""

    def __init__(self, runs: In_Memory_GradingRunRepository | None = None) -> None:
        self._sessions: dict[StableId, LabSession] = {}
        self._runs = runs
        self._lock = threading.RLock()

    def add_if_no_active(self, session: LabSession) -> None:
        with self._lock:
            if session.stable_id in self._sessions:
                raise ValueError(f"session exists: {session.stable_id}")
            if self.get_active_for_user(session.user_id) is not None:
                raise ActiveSessionExists(session.user_id)
            self._sessions[session.stable_id] = session

    def get(self, session_id: StableId) -> LabSession | None:
        return self._sessions.get(session_id)

    def get_for_owner(self, session_id: StableId, user_id: StableId) -> LabSession | None:
        session = self._sessions.get(session_id)
        return session if session is not None and session.user_id == user_id else None

    def get_active_for_user(self, user_id: StableId) -> LabSession | None:
        for s in self._sessions.values():
            if s.user_id == user_id and s.status.is_active:
                return s
        return None

    def transition(
        self,
        session_id: StableId,
        *,
        expected: Collection[SessionStatus],
        to: SessionStatus,
        **fields: object,
    ) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.status not in expected:
                return False
            self._sessions[session_id] = replace(s, status=to, **fields)  # type: ignore[arg-type]
            return True

    def try_accept_grading(
        self, session_id: StableId, *, now: datetime, min_interval: timedelta
    ) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.status is not SessionStatus.READY:
                return False
            last = s.last_grading_accepted_at
            if last is not None and not (now - last > min_interval):
                return False
            self._sessions[session_id] = replace(
                s, status=SessionStatus.GRADING, last_grading_accepted_at=now
            )
            return True

    def claim_provision_attempt(
        self, session_id: StableId, *, expected_attempts: int, now: datetime
    ) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if (
                s is None
                or s.status is not SessionStatus.PROVISIONING
                or s.provision_attempts != expected_attempts
            ):
                return False
            self._sessions[session_id] = replace(s, provision_attempts=expected_attempts + 1)
            return True

    def touch_activity(self, session_id: StableId, at: datetime) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return
            if s.last_activity_at is None or at > s.last_activity_at:
                self._sessions[session_id] = replace(s, last_activity_at=at)

    def bump_terminal_epoch(self, session_id: StableId) -> int | None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                return None
            epoch = s.terminal_epoch + 1
            self._sessions[session_id] = replace(s, terminal_epoch=epoch)
            return epoch

    def count_started_since(self, user_id: StableId, since: datetime) -> int:
        return sum(
            1 for s in self._sessions.values() if s.user_id == user_id and s.created_at >= since
        )

    def list_for_user_page(
        self, user_id: StableId, *, after: Keyset | None = None, limit: int
    ) -> tuple[LabSession, ...]:
        rows = sorted(
            (s for s in self._sessions.values() if s.user_id == user_id),
            key=lambda s: (s.created_at, s.stable_id),
        )
        if after is not None:
            rows = [s for s in rows if (s.created_at, s.stable_id) > after]
        return tuple(rows[:limit])

    def list_active(self) -> Sequence[LabSession]:
        return tuple(s for s in self._sessions.values() if s.status.is_active)

    def list_ended_before(self, cutoff: datetime) -> Sequence[LabSession]:
        return tuple(
            s
            for s in self._sessions.values()
            if s.status in _TERMINAL and s.ended_at is not None and s.ended_at < cutoff
        )

    def delete_session_data(self, session_ids: Collection[StableId]) -> int:
        with self._lock:
            ids = set(session_ids)
            if self._runs is not None:
                self._runs.delete_for_sessions(ids)
            doomed = [i for i in ids if i in self._sessions]
            for i in doomed:
                del self._sessions[i]
            return len(doomed)
