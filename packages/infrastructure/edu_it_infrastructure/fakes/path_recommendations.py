"""In-memory ``PathRecommendationRepository`` (ai-content task 22.2)."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.ports.path_recommendations import AdvisorAuditEntry, PathRecommendation

__all__ = ["In_Memory_PathRecommendationRepository"]


class In_Memory_PathRecommendationRepository:
    """Mirrors the SQL adapter: unique ``stable_id``, atomic run record, idempotent dismissal."""

    def __init__(self) -> None:
        self._recommendations: dict[str, PathRecommendation] = {}
        self._audit: list[AdvisorAuditEntry] = []
        self._lock = threading.RLock()

    def record_run(self, recommendation: PathRecommendation, audit: AdvisorAuditEntry) -> None:
        with self._lock:
            if recommendation.stable_id in self._recommendations:
                raise ValueError(f"duplicate recommendation {recommendation.stable_id!r}")
            if any(a.stable_id == audit.stable_id for a in self._audit):
                raise ValueError(f"duplicate audit entry {audit.stable_id!r}")
            self._recommendations[recommendation.stable_id] = recommendation
            self._audit.append(audit)

    def get(self, stable_id: str) -> PathRecommendation | None:
        with self._lock:
            return self._recommendations.get(stable_id)

    def latest_undismissed(self, user_id: str) -> PathRecommendation | None:
        with self._lock:
            mine = [
                r
                for r in self._recommendations.values()
                if r.user_id == user_id and r.dismissed_at is None
            ]
            if not mine:
                return None
            # Insertion order breaks a tie on the timestamp, like the id in SQL.
            newest = max(enumerate(mine), key=lambda pair: (pair[1].created_at, pair[0]))
            return newest[1]

    def dismiss(self, stable_id: str, user_id: str, at: datetime) -> PathRecommendation | None:
        with self._lock:
            current = self._recommendations.get(stable_id)
            if current is None or current.user_id != user_id:
                return None
            if current.dismissed_at is None:
                current = replace(current, dismissed_at=at)
                self._recommendations[stable_id] = current
            return current

    def audit_entries(self, user_id: str) -> tuple[AdvisorAuditEntry, ...]:
        with self._lock:
            return tuple(a for a in self._audit if a.user_id == user_id)

    def delete_audit_before(self, cutoff: datetime) -> int:
        with self._lock:
            kept = [a for a in self._audit if a.recorded_at >= cutoff]
            removed = len(self._audit) - len(kept)
            self._audit = kept
            return removed

    def delete_superseded_before(self, cutoff: datetime) -> int:
        with self._lock:
            # The newest per learner (insertion order breaks a timestamp tie) is never superseded.
            newest: dict[str, tuple[datetime, int]] = {}
            for index, rec in enumerate(self._recommendations.values()):
                best = newest.get(rec.user_id)
                if best is None or (rec.created_at, index) > best:
                    newest[rec.user_id] = (rec.created_at, index)
            doomed = [
                sid
                for index, (sid, rec) in enumerate(self._recommendations.items())
                if rec.created_at < cutoff and newest[rec.user_id] != (rec.created_at, index)
            ]
            for sid in doomed:
                del self._recommendations[sid]
            return len(doomed)

    def delete_for_user(self, user_id: str) -> int:
        with self._lock:
            doomed = [sid for sid, r in self._recommendations.items() if r.user_id == user_id]
            for sid in doomed:
                del self._recommendations[sid]
            kept = [a for a in self._audit if a.user_id != user_id]
            removed = len(self._audit) - len(kept) + len(doomed)
            self._audit = kept
            return removed
