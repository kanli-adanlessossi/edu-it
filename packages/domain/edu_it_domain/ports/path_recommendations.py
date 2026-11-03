"""The ``PathRecommendationRepository`` port (ai-content Req 17.2, 17.6, 17.8, 21.x).

A path recommendation is the ordered list of concept ids the Path_Advisor suggests to one
learner, with its ``source`` (``llm`` or ``rule-based``) and the ``run_id`` of the advisor run
that produced it. A learner dismisses a recommendation; the dismissal is a timestamp on the
same row. Every advisor run also appends exactly one audit entry (Advisor_Audit_Log) holding
the per-run Pseudonymous_Handle, the source, the outcome and the identifiers the model
returned.

Both records link to a learner, so they are personal data: registered for export, deletion
(with the learner) and retention. The run writes the recommendation and its audit entry in one
operation, so a run never leaves one without the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "SOURCE_LLM",
    "SOURCE_RULE_BASED",
    "AdvisorAuditEntry",
    "PathRecommendation",
    "PathRecommendationRepository",
]

#: The recommendation came from an accepted model order.
SOURCE_LLM: Final[str] = "llm"

#: The recommendation is the deterministic fallback order.
SOURCE_RULE_BASED: Final[str] = "rule-based"


@dataclass(frozen=True, slots=True)
class PathRecommendation:
    """One persisted recommendation."""

    stable_id: str
    user_id: str
    source: str
    ordering: tuple[str, ...]
    run_id: str
    created_at: datetime
    dismissed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdvisorAuditEntry:
    """One Advisor_Audit_Log row: what a run did, with no model text and no identity."""

    stable_id: str
    user_id: str
    run_id: str
    handle: str
    source: str
    outcome: str
    recorded_at: datetime
    reason: str | None = None
    returned_ids: tuple[str, ...] = ()


@runtime_checkable
class PathRecommendationRepository(Protocol):
    """Storage of path recommendations and their audit log."""

    def record_run(self, recommendation: PathRecommendation, audit: AdvisorAuditEntry) -> None:
        """Store the recommendation and its audit entry together, or neither.

        ``ValueError`` for an unknown learner or a repeated ``stable_id``.
        """
        ...

    def get(self, stable_id: str) -> PathRecommendation | None:
        """The recommendation with this ``stable_id``, or ``None``."""
        ...

    def latest_undismissed(self, user_id: str) -> PathRecommendation | None:
        """The learner's newest recommendation that is not dismissed, or ``None``."""
        ...

    def dismiss(self, stable_id: str, user_id: str, at: datetime) -> PathRecommendation | None:
        """Dismiss the learner's recommendation and return it, or ``None`` if it is not theirs.

        Idempotent: an already dismissed recommendation keeps its first ``dismissed_at``.
        """
        ...

    def audit_entries(self, user_id: str) -> tuple[AdvisorAuditEntry, ...]:
        """The learner's audit entries, oldest first."""
        ...

    def delete_audit_before(self, cutoff: datetime) -> int:
        """Delete every audit entry with ``recorded_at < cutoff``; return the row count."""
        ...

    def delete_superseded_before(self, cutoff: datetime) -> int:
        """Delete recommendations older than ``cutoff`` that a newer one of the learner replaced.

        The learner's newest recommendation is never deleted here, whatever its age, so a
        learner without a newer run keeps the one they can still see. Returns the row count.
        """
        ...

    def delete_for_user(self, user_id: str) -> int:
        """Delete every recommendation and audit entry of the learner; return the row count."""
        ...
