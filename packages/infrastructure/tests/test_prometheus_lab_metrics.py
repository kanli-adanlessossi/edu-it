"""Unit tests for the Prometheus LabMetrics implementation (kubernetes-deployment task 10.2)."""

from __future__ import annotations

import pytest
from edu_it_domain.ports.lab_metrics import LabMetrics
from edu_it_infrastructure.telemetry import PrometheusLabMetrics
from prometheus_client import CollectorRegistry, generate_latest

# The closed label allowlist of the design (Req 13.12).
_ALLOWED_LABELS = {"reason", "result", "provider"}


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> PrometheusLabMetrics:
    return PrometheusLabMetrics(registry)


# Claims: 13.3
def test_satisfies_the_port(metrics: PrometheusLabMetrics) -> None:
    assert isinstance(metrics, LabMetrics)


# Claims: 13.3, 13.4
def test_gauge_counters_and_histograms_record_values(
    metrics: PrometheusLabMetrics, registry: CollectorRegistry
) -> None:
    metrics.set_active_sessions(3)
    metrics.observe_provision_duration(1.5, result="ok", provider="kubernetes")
    metrics.inc_provision_failure(reason="capacity")
    metrics.inc_provision_failure(reason="capacity")
    metrics.observe_grading_duration(2.0, result="passed")
    metrics.inc_reaper_deletion(reason="expired")
    metrics.inc_reaper_deletion(reason="orphan")
    metrics.inc_reaper_deletion(reason="evicted")
    metrics.inc_quota_rejection()
    metrics.inc_infrastructure_loss()

    get = registry.get_sample_value
    assert get("edu_it_lab_active_sessions") == 3
    assert get("edu_it_lab_provision_failures_total", {"reason": "capacity"}) == 2
    assert get("edu_it_lab_reaper_deletions_total", {"reason": "expired"}) == 1
    assert get("edu_it_lab_reaper_deletions_total", {"reason": "orphan"}) == 1
    assert get("edu_it_lab_reaper_deletions_total", {"reason": "evicted"}) == 1
    assert get("edu_it_lab_quota_rejections_total") == 1
    assert get("edu_it_lab_infrastructure_loss_total") == 1
    assert (
        get(
            "edu_it_lab_provision_duration_seconds_count",
            {"result": "ok", "provider": "kubernetes"},
        )
        == 1
    )
    assert (
        get(
            "edu_it_lab_provision_duration_seconds_sum",
            {"result": "ok", "provider": "kubernetes"},
        )
        == 1.5
    )
    assert get("edu_it_lab_grading_duration_seconds_count", {"result": "passed"}) == 1
    assert get("edu_it_lab_grading_duration_seconds_sum", {"result": "passed"}) == 2.0


# Claims: 13.3
def test_active_sessions_gauge_can_go_down(
    metrics: PrometheusLabMetrics, registry: CollectorRegistry
) -> None:
    metrics.set_active_sessions(5)
    metrics.set_active_sessions(2)
    assert registry.get_sample_value("edu_it_lab_active_sessions") == 2


# Claims: 13.4
def test_exposition_contains_every_lab_metric(
    metrics: PrometheusLabMetrics, registry: CollectorRegistry
) -> None:
    text = generate_latest(registry).decode()
    for name in (
        "edu_it_lab_active_sessions",
        "edu_it_lab_provision_duration_seconds",
        "edu_it_lab_provision_failures_total",
        "edu_it_lab_grading_duration_seconds",
        "edu_it_lab_reaper_deletions_total",
        "edu_it_lab_quota_rejections_total",
        "edu_it_lab_infrastructure_loss_total",
    ):
        assert f"# TYPE {name}" in text, name


# Claims: 13.12
def test_label_names_come_from_the_closed_allowlist(
    metrics: PrometheusLabMetrics, registry: CollectorRegistry
) -> None:
    metrics.observe_provision_duration(1.0, result="ok", provider="docker")
    metrics.inc_provision_failure(reason="timeout")
    metrics.observe_grading_duration(1.0, result="failed")
    metrics.inc_reaper_deletion(reason="orphan")
    used: set[str] = set()
    for family in registry.collect():
        for sample in family.samples:
            used |= set(sample.labels) - {"le"}  # "le" is the histogram bucket bound
    assert used <= _ALLOWED_LABELS
    assert used == _ALLOWED_LABELS


# Claims: 13.12
def test_learner_identifier_is_not_a_label_and_is_rejected_as_an_argument(
    metrics: PrometheusLabMetrics,
) -> None:
    with pytest.raises(TypeError):
        metrics.inc_provision_failure(reason="capacity", learner_id="u-1")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        metrics.observe_grading_duration(1.0, result="ok", learner_id="u-1")  # type: ignore[call-arg]


# Claims: 13.3
def test_two_instances_on_separate_registries_do_not_collide() -> None:
    a, b = CollectorRegistry(), CollectorRegistry()
    PrometheusLabMetrics(a).inc_quota_rejection()
    PrometheusLabMetrics(b)
    assert a.get_sample_value("edu_it_lab_quota_rejections_total") == 1
    assert b.get_sample_value("edu_it_lab_quota_rejections_total") == 0
