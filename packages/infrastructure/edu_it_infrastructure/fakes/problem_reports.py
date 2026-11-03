"""In-memory ``ProblemReportRepository`` (ai-content task 20.1)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.ports.item_flags import ItemKey
    from edu_it_domain.ports.problem_reports import ProblemReport

__all__ = ["In_Memory_ProblemReportRepository"]


class In_Memory_ProblemReportRepository:
    """Mirrors the SQL adapter: unique ``stable_id``, per-learner deletion."""

    def __init__(self) -> None:
        self._reports: dict[str, ProblemReport] = {}
        self._lock = threading.RLock()

    def add(self, report: ProblemReport) -> None:
        with self._lock:
            if report.stable_id in self._reports:
                raise ValueError(f"duplicate problem report {report.stable_id!r}")
            self._reports[report.stable_id] = report

    def get(self, stable_id: str) -> ProblemReport | None:
        with self._lock:
            return self._reports.get(stable_id)

    def reporters_for(self, key: ItemKey, content_version: str) -> frozenset[str]:
        with self._lock:
            return frozenset(
                r.user_id
                for r in self._reports.values()
                if r.key == key and r.content_version == content_version
            )

    def delete_created_before(self, cutoff: datetime) -> int:
        with self._lock:
            doomed = [sid for sid, r in self._reports.items() if r.created_at < cutoff]
            for sid in doomed:
                del self._reports[sid]
            return len(doomed)

    def delete_for_user(self, user_id: str) -> int:
        with self._lock:
            doomed = [sid for sid, r in self._reports.items() if r.user_id == user_id]
            for sid in doomed:
                del self._reports[sid]
            return len(doomed)
