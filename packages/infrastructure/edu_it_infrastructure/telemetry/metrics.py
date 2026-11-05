"""Prometheus ``LabMetrics`` implementation (kubernetes-deployment task 10.2).

Covers Req 13.3, 13.4 and 13.12. :class:`PrometheusLabMetrics` implements the domain
``LabMetrics`` port with ``prometheus_client`` collectors served on the dedicated metrics port
(Req 13.3). Label names come from the closed allowlist named by the design (``reason``,
``result``, ``provider``); no learner identifier, or any other learner personal data, is ever
used as a label (Req 13.12).

The metric names and types follow the design table exactly:

* ``edu_it_lab_active_sessions``: gauge
* ``edu_it_lab_provision_duration_seconds``: histogram, labels ``result``, ``provider``
* ``edu_it_lab_provision_failures_total``: counter, label ``reason``
* ``edu_it_lab_grading_duration_seconds``: histogram, label ``result``
* ``edu_it_lab_reaper_deletions_total``: counter, label ``reason``
* ``edu_it_lab_quota_rejections_total``: counter, no labels
* ``edu_it_lab_infrastructure_loss_total``: counter, no labels

By default the collectors register in the ``prometheus_client`` default registry, so construct one
instance per process. Tests pass their own :class:`~prometheus_client.CollectorRegistry` to avoid
duplicate-registration errors.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

from edu_it_domain.ports.lab_metrics import LabMetrics
from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

if TYPE_CHECKING:
    from threading import Thread
    from wsgiref.simple_server import WSGIServer

__all__ = [
    "MetricsPortError",
    "PrometheusLabMetrics",
    "resolve_metrics_port",
    "start_metrics_server",
]


class MetricsPortError(ValueError):
    """The metrics port setting is malformed or collides with a traffic port."""


def resolve_metrics_port(
    environ: Mapping[str, str],
    variable: str,
    *,
    default: int,
    traffic_ports: Collection[int],
) -> int:
    """Read the dedicated metrics port from ``variable`` (Req 13.3).

    The metrics port is separate from the traffic ports: a value in ``traffic_ports`` (or
    outside ``1..65535``, or not an integer) raises :class:`MetricsPortError`. The message
    names the variable only, never the value.
    """
    raw = environ.get(variable)
    if raw is None or raw.strip() == "":
        port = default
    else:
        try:
            port = int(raw)
        except ValueError as exc:
            raise MetricsPortError(f"{variable} must be an integer port") from exc
    if not 1 <= port <= 65535:
        raise MetricsPortError(f"{variable} must be in 1..65535")
    if port in traffic_ports:
        raise MetricsPortError(f"{variable} must differ from the traffic ports")
    return port


def start_metrics_server(
    port: int, *, host: str = "127.0.0.1", registry: CollectorRegistry | None = None
) -> tuple[WSGIServer, Thread]:
    """Serve ``/metrics`` on its own port from a daemon thread (Req 13.3).

    The caller stops it with ``server.shutdown(); server.server_close()``.
    """
    return start_http_server(port, addr=host, registry=REGISTRY if registry is None else registry)


class PrometheusLabMetrics(LabMetrics):
    """Lab_Metrics backed by ``prometheus_client`` collectors (Req 13.3, 13.4, 13.12)."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        reg = registry if registry is not None else REGISTRY

        self._active_sessions = Gauge(
            "edu_it_lab_active_sessions",
            "Number of currently active lab sessions.",
            registry=reg,
        )
        self._provision_duration = Histogram(
            "edu_it_lab_provision_duration_seconds",
            "Lab provisioning duration in seconds.",
            labelnames=("result", "provider"),
            registry=reg,
        )
        self._provision_failures = Counter(
            "edu_it_lab_provision_failures_total",
            "Count of lab provisioning failures.",
            labelnames=("reason",),
            registry=reg,
        )
        self._grading_duration = Histogram(
            "edu_it_lab_grading_duration_seconds",
            "Lab grading duration in seconds.",
            labelnames=("result",),
            registry=reg,
        )
        self._reaper_deletions = Counter(
            "edu_it_lab_reaper_deletions_total",
            "Count of lab pods deleted by the reaper.",
            labelnames=("reason",),
            registry=reg,
        )
        self._quota_rejections = Counter(
            "edu_it_lab_quota_rejections_total",
            "Count of lab provisioning attempts rejected for capacity.",
            registry=reg,
        )
        self._infrastructure_loss = Counter(
            "edu_it_lab_infrastructure_loss_total",
            "Count of lab sessions ended by infrastructure loss.",
            registry=reg,
        )

    def set_active_sessions(self, count: int) -> None:
        self._active_sessions.set(count)

    def observe_provision_duration(self, seconds: float, *, result: str, provider: str) -> None:
        self._provision_duration.labels(result=result, provider=provider).observe(seconds)

    def inc_provision_failure(self, *, reason: str) -> None:
        self._provision_failures.labels(reason=reason).inc()

    def observe_grading_duration(self, seconds: float, *, result: str) -> None:
        self._grading_duration.labels(result=result).observe(seconds)

    def inc_reaper_deletion(self, *, reason: str) -> None:
        self._reaper_deletions.labels(reason=reason).inc()

    def inc_quota_rejection(self) -> None:
        self._quota_rejections.inc()

    def inc_infrastructure_loss(self) -> None:
        self._infrastructure_loss.inc()
