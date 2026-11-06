"""The dedicated metrics port: resolution rules and the served ``/metrics`` endpoint.

**Validates: Requirements 13.3, 13.4**
"""

from __future__ import annotations

import socket
import urllib.request

import pytest
from edu_it_infrastructure.telemetry import (
    MetricsPortError,
    PrometheusLabMetrics,
    resolve_metrics_port,
    start_metrics_server,
)
from prometheus_client import CollectorRegistry


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_port_defaults_when_unset_or_blank() -> None:
    assert resolve_metrics_port({}, "X", default=9090, traffic_ports={8000}) == 9090
    assert resolve_metrics_port({"X": " "}, "X", default=9090, traffic_ports={8000}) == 9090


def test_port_is_read_from_the_variable() -> None:
    assert resolve_metrics_port({"X": "9191"}, "X", default=9090, traffic_ports=set()) == 9191


@pytest.mark.parametrize("value", ["abc", "0", "70000", "-1", "8000"])
def test_invalid_or_colliding_port_is_refused_without_echoing_the_value(value: str) -> None:
    with pytest.raises(MetricsPortError) as raised:
        resolve_metrics_port({"X": value}, "X", default=9090, traffic_ports={8000})
    assert "X" in str(raised.value)
    assert value not in str(raised.value) or value == "X"


def test_metrics_endpoint_serves_the_lab_metrics_on_its_own_port() -> None:
    registry = CollectorRegistry()
    metrics = PrometheusLabMetrics(registry)
    metrics.inc_quota_rejection()
    port = _free_port()

    server, _thread = start_metrics_server(port, registry=registry)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            body = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()

    assert "edu_it_lab_quota_rejections_total 1.0" in body
