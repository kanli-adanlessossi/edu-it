"""Telemetry adapters: JSON logging with redaction (Req 13.8, 13.11) and a bounded
OpenTelemetry trace exporter (Req 13.1, 13.2)."""

from edu_it_infrastructure.telemetry.logging import (
    REDACTED,
    JsonLogFormatter,
    RedactionFilter,
    bind_lab_session_id,
    bind_request_id,
    configure_logging,
    current_lab_session_id,
    current_request_id,
    redact_text,
    redact_value,
    reset_lab_session_id,
    reset_request_id,
)
from edu_it_infrastructure.telemetry.metrics import (
    MetricsPortError,
    PrometheusLabMetrics,
    resolve_metrics_port,
    start_metrics_server,
)
from edu_it_infrastructure.telemetry.propagation import (
    REQUEST_ID_ENVELOPE_KEY,
    RequestIdTaskQueue,
    bind_request_id_from_envelope,
    generate_request_id,
    with_request_id,
)
from edu_it_infrastructure.telemetry.tracing import (
    OTLP_ENDPOINT_ENV,
    BoundedSpanExporter,
    configure_tracing,
    configure_tracing_from_env,
    shutdown_tracing,
    traces_url,
)

__all__ = [
    "OTLP_ENDPOINT_ENV",
    "REDACTED",
    "REQUEST_ID_ENVELOPE_KEY",
    "BoundedSpanExporter",
    "JsonLogFormatter",
    "MetricsPortError",
    "PrometheusLabMetrics",
    "RedactionFilter",
    "RequestIdTaskQueue",
    "bind_lab_session_id",
    "bind_request_id",
    "bind_request_id_from_envelope",
    "configure_logging",
    "configure_tracing",
    "configure_tracing_from_env",
    "current_lab_session_id",
    "current_request_id",
    "generate_request_id",
    "redact_text",
    "redact_value",
    "reset_lab_session_id",
    "reset_request_id",
    "resolve_metrics_port",
    "shutdown_tracing",
    "start_metrics_server",
    "traces_url",
    "with_request_id",
]
