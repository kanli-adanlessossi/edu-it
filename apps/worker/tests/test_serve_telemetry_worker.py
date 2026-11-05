"""Worker telemetry wiring: metrics port and the metrics sink reaching the lab worker.

**Validates: Requirements 13.1, 13.3, 13.4**
"""

from __future__ import annotations

import socket
import urllib.request
from pathlib import Path

import pytest
from edu_it_infrastructure.telemetry import PrometheusLabMetrics
from edu_it_worker import serve
from edu_it_worker.lifecycle import StartupRefused
from prometheus_client import CollectorRegistry


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_telemetry_serves_metrics_on_the_configured_port() -> None:
    port = _free_port()
    started = serve.start_telemetry(
        {"WORKER_METRICS_PORT": str(port)}, traffic_ports={8001}, registry=CollectorRegistry()
    )
    try:
        started.metrics.inc_infrastructure_loss()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            assert "edu_it_lab_infrastructure_loss_total 1.0" in response.read().decode()
        assert started.tracer_provider is None
    finally:
        started.shutdown()


def test_metrics_port_equal_to_the_health_port_refuses_the_start() -> None:
    with pytest.raises(StartupRefused):
        serve.start_telemetry(
            {"WORKER_METRICS_PORT": "8001"}, traffic_ports={8001}, registry=CollectorRegistry()
        )


def test_build_served_worker_passes_the_metrics_to_the_lab_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content_root = Path(__file__).resolve().parents[3] / "content"
    monkeypatch.setenv("EDU_IT_CONTENT_ROOT", str(content_root))
    seen: dict[str, object] = {}
    real = serve.build_lab_worker

    def spy(**kwargs: object):  # type: ignore[no-untyped-def]
        seen["metrics"] = kwargs.get("metrics")
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(serve, "build_lab_worker", spy)
    metrics = PrometheusLabMetrics(CollectorRegistry())

    serve.build_served_worker(metrics=metrics)

    assert seen["metrics"] is metrics
