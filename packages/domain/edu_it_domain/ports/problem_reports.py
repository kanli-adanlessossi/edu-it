"""The ``ProblemReportRepository`` port (ai-content Req 18.2, 21.1, 21.2, 21.4, 21.6; task 20.1).

A Problem_Report is a learner report on a question, lesson or lab. It is stored with
the item's ``stable_id`` and the ``content_version`` the learner saw, a category and
an optional comment. The comment is free text from a learner: it is personal data and
has no path to any ``LLMInput``. ``problem_report`` therefore holds a learner
reference and is registered for export, deletion and retention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.ports.item_flags import ItemKey

__all__ = ["ProblemReport", "ProblemReportRepository"]


@dataclass(frozen=True, slots=True)
class ProblemReport:
    """One stored report."""

    stable_id: str
    user_id: str
    key: ItemKey
    content_version: str
    category: str
    created_at: datetime
    comment: str | None = None


@runtime_checkable
class ProblemReportRepository(Protocol):
    """Storage of problem reports."""

    def add(self, report: ProblemReport) -> None:
        """Store the report. ``ValueError`` for an unknown learner or a repeated ``stable_id``."""
        ...

    def get(self, stable_id: str) -> ProblemReport | None:
        """The report with this ``stable_id``, or ``None``."""
        ...

    def reporters_for(self, key: ItemKey, content_version: str) -> frozenset[str]:
        """The distinct learners who reported ``key`` at exactly ``content_version``."""
        ...

    def delete_created_before(self, cutoff: datetime) -> int:
        """Delete every report with ``created_at < cutoff`` and return how many were removed."""
        ...

    def delete_for_user(self, user_id: str) -> int:
        """Delete every report of the learner and return how many were removed."""
        ...
