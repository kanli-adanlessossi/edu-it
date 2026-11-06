"""Api telemetry wiring: metrics port, tracing from env, shared log formatter.

**Validates: Requirements 13.1, 13.3, 13.8**
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.request

import pytest
from edu_it_api import logging as api_logging
from edu_it_api.serve import start_telemetry
from edu_it_api.startup import StartupRefused
from edu_it_infrastructure import telemetry as shared
from prometheus_client import CollectorRegistry


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_start_telemetry_serves_metrics_on_the_configured_port() -> None:
    port = _free_port()
    started = start_telemetry(
        {"API_METRICS_PORT": str(port)}, traffic_ports={8000}, registry=CollectorRegistry()
    )
    try:
        started.metrics.inc_quota_rejection()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            assert "edu_it_lab_quota_rejections_total 1.0" in response.read().decode()
        assert started.tracer_provider is None  # no OTEL endpoint: nothing is built
    finally:
        started.shutdown()


def test_metrics_port_equal_to_a_traffic_port_refuses_the_start() -> None:
    with pytest.raises(StartupRefused):
        start_telemetry(
            {"API_METRICS_PORT": "8000"}, traffic_ports={8000}, registry=CollectorRegistry()
        )


def test_api_logging_uses_the_shared_formatter() -> None:
    assert api_logging.JsonLogFormatter is shared.JsonLogFormatter
    api_logging.configure_logging()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler.formatter, shared.JsonLogFormatter)
    record = logging.makeLogRecord({"msg": "hi", "levelno": logging.INFO, "levelname": "INFO"})
    assert json.loads(handler.formatter.format(record))["message"] == "hi"
