"""No-op fake of the domain ``LabMetrics`` port."""

from __future__ import annotations

from edu_it_domain.ports.lab_metrics import LabMetrics

__all__ = ["NoopLabMetrics"]


class NoopLabMetrics(LabMetrics):
    """Discards every metric; the default where no telemetry is wired."""

    def set_active_sessions(self, count: int) -> None:
        return None

    def observe_provision_duration(self, seconds: float, *, result: str, provider: str) -> None:
        return None

    def inc_provision_failure(self, *, reason: str) -> None:
        return None

    def observe_grading_duration(self, seconds: float, *, result: str) -> None:
        return None

    def inc_reaper_deletion(self, *, reason: str) -> None:
        return None

    def inc_quota_rejection(self) -> None:
        return None

    def inc_infrastructure_loss(self) -> None:
        return None
