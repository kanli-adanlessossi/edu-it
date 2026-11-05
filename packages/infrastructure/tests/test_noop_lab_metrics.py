"""The no-op LabMetrics fake satisfies the domain port."""

from __future__ import annotations

from edu_it_domain.ports.lab_metrics import LabMetrics
from edu_it_infrastructure.fakes.lab_metrics import NoopLabMetrics


def test_noop_metrics_satisfies_port_and_accepts_every_call() -> None:
    m = NoopLabMetrics()
    assert isinstance(m, LabMetrics)
    m.set_active_sessions(1)
    m.observe_provision_duration(1.0, result="ok", provider="kubernetes")
    m.inc_provision_failure(reason="capacity")
    m.observe_grading_duration(1.0, result="passed")
    m.inc_reaper_deletion(reason="evicted")
    m.inc_quota_rejection()
    m.inc_infrastructure_loss()
