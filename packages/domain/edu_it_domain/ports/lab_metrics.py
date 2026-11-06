"""The ``LabMetrics`` port (kubernetes-deployment Req 13.3, 13.4).

Use cases report lab events through this port; a Prometheus implementation is
infrastructure. Label values come from a closed allowlist (``reason``,
``result``, ``provider``) and never include a learner identifier. This module
imports only the standard library.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["LabMetrics"]


@runtime_checkable
class LabMetrics(Protocol):
    """Lab metrics sink."""

    def set_active_sessions(self, count: int) -> None:
        """Set the ``edu_it_lab_active_sessions`` gauge."""
        ...

    def observe_provision_duration(self, seconds: float, *, result: str, provider: str) -> None:
        """Record one provisioning duration (``edu_it_lab_provision_duration_seconds``)."""
        ...

    def inc_provision_failure(self, *, reason: str) -> None:
        """Count one provisioning failure (``edu_it_lab_provision_failures_total``)."""
        ...

    def observe_grading_duration(self, seconds: float, *, result: str) -> None:
        """Record one grading duration (``edu_it_lab_grading_duration_seconds``)."""
        ...

    def inc_reaper_deletion(self, *, reason: str) -> None:
        """Count one reaper deletion: ``expired``, ``orphan`` or ``evicted``."""
        ...

    def inc_quota_rejection(self) -> None:
        """Count one quota-rejected attempt (``edu_it_lab_quota_rejections_total``)."""
        ...

    def inc_infrastructure_loss(self) -> None:
        """Count one infrastructure loss (``edu_it_lab_infrastructure_loss_total``)."""
        ...
