"""The ``PurgeAiData`` retention logic (ai-content Req 21.4; task 23.1).

Applies the retention periods registered with each AI personal-data table
(``AI_RETENTION_OBSERVATION``, ``AI_RETENTION_PROBLEM_REPORT``, ``AI_RETENTION_AUDIT`` and
``AI_RETENTION_RECOMMENDATION``) to ``item_observation``, ``problem_report``,
``advisor_audit_log`` and superseded ``path_recommendation`` rows. A row is deleted when it is
strictly older than its period; a learner's newest recommendation is never purged. The
``item_statistic`` aggregates carry no learner link and are never touched. The job is
idempotent: a second run at the same Clock instant finds nothing left to delete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from edu_it_domain import config as domain_config

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.item_quality import ItemQualityRepository
    from edu_it_domain.ports.path_recommendations import PathRecommendationRepository
    from edu_it_domain.ports.problem_reports import ProblemReportRepository

__all__ = ["PurgeAiData", "PurgeAiDataResult"]


@dataclass(frozen=True, slots=True)
class PurgeAiDataResult:
    """How many rows one run deleted from each table."""

    observations: int
    problem_reports: int
    audit_entries: int
    recommendations: int

    @property
    def total(self) -> int:
        """All rows deleted by the run."""
        return self.observations + self.problem_reports + self.audit_entries + self.recommendations


class PurgeAiData:
    """Delete AI personal data older than its retention period."""

    def __init__(
        self,
        *,
        quality: ItemQualityRepository,
        reports: ProblemReportRepository,
        recommendations: PathRecommendationRepository,
        clock: Clock,
        config: object = domain_config,
    ) -> None:
        """Wire the repositories that own the four tables and the clock."""
        self._quality = quality
        self._reports = reports
        self._recommendations = recommendations
        self._clock = clock
        self._config = config

    def execute(self) -> PurgeAiDataResult:
        """Purge the expired rows and return the per-table counts."""
        now = self._clock.now()
        cfg = self._config
        return PurgeAiDataResult(
            observations=self._quality.delete_observations_before(
                now - cfg.AI_RETENTION_OBSERVATION  # type: ignore[attr-defined]
            ),
            problem_reports=self._reports.delete_created_before(
                now - cfg.AI_RETENTION_PROBLEM_REPORT  # type: ignore[attr-defined]
            ),
            audit_entries=self._recommendations.delete_audit_before(
                now - cfg.AI_RETENTION_AUDIT  # type: ignore[attr-defined]
            ),
            recommendations=self._recommendations.delete_superseded_before(
                now - cfg.AI_RETENTION_RECOMMENDATION  # type: ignore[attr-defined]
            ),
        )
