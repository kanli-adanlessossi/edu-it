"""OpenTelemetry tracing with a bounded exporter (kubernetes-deployment task 10.3, Req 13.1, 13.2).

The Api and the Worker export traces **only when a collector endpoint is set**
(``OTEL_EXPORTER_OTLP_ENDPOINT``, filled from the Values_File by the chart, Req 13.1). With no
endpoint :func:`configure_tracing` builds nothing: no provider, no exporter, no thread.

Export can never slow a request or a job down, and a broken collector can never grow memory
(Req 13.2):

* spans go through a ``BatchSpanProcessor``: ending a span only appends it to a **bounded queue**
  and a **background thread** exports batches. A full queue drops the new span instead of
  blocking the caller;
* :class:`BoundedSpanExporter` wraps the OTLP exporter and tries one batch **at most**
  :data:`~edu_it_domain.config.TRACE_EXPORT_MAX_ATTEMPTS` (3) times, each attempt limited to
  :data:`~edu_it_domain.config.TRACE_EXPORT_ATTEMPT_TIMEOUT`, then **drops** the batch and goes on
  with the next one.

Log records written here carry counts only, never span names or attributes, so no secret,
terminal data or learner personal data reaches the logs (Req 13.11). All numbers live in
``domain/config.py``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Final

from edu_it_domain import config
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import SpanProcessor

__all__ = [
    "OTLP_ENDPOINT_ENV",
    "BoundedSpanExporter",
    "configure_tracing",
    "configure_tracing_from_env",
    "shutdown_tracing",
    "traces_url",
]

_LOG = logging.getLogger(__name__)

#: Environment variable that carries the collector endpoint (OpenTelemetry standard name).
OTLP_ENDPOINT_ENV: Final[str] = "OTEL_EXPORTER_OTLP_ENDPOINT"

_TRACES_PATH: Final[str] = "v1/traces"


class BoundedSpanExporter(SpanExporter):
    """Wrap an exporter so one batch is tried at most ``max_attempts`` times, then dropped.

    ``export`` never raises: an exception of the wrapped exporter counts as a failed attempt.
    After the last failed attempt the batch is dropped, one warning with the span count is
    logged and ``FAILURE`` is returned, so the processor moves on to the next batch.
    """

    def __init__(
        self,
        inner: SpanExporter,
        *,
        max_attempts: int = config.TRACE_EXPORT_MAX_ATTEMPTS,
        backoff_seconds: float = config.TRACE_EXPORT_RETRY_BACKOFF.total_seconds(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")
        self._inner = inner
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for attempt in range(1, self._max_attempts + 1):
            try:
                if self._inner.export(spans) is SpanExportResult.SUCCESS:
                    return SpanExportResult.SUCCESS
            except Exception as error:  # an exporter fault must never reach the caller
                _LOG.debug("trace export attempt %d raised %s", attempt, type(error).__name__)
            if attempt < self._max_attempts:
                self._sleep(self._backoff_seconds)
        _LOG.warning(
            "dropped %d span(s) after %d failed export attempt(s)", len(spans), self._max_attempts
        )
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as error:  # shutdown is best effort
            _LOG.debug("trace exporter shutdown raised %s", type(error).__name__)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:
            return False


def traces_url(endpoint: str) -> str:
    """Return the OTLP/HTTP traces URL for a collector endpoint.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a base URL (``http://collector:4318``); the exporter wants
    the full signal URL, so ``/v1/traces`` is appended unless it is already there.
    """
    base = endpoint.strip().rstrip("/")
    return base if base.endswith(_TRACES_PATH) else f"{base}/{_TRACES_PATH}"


def configure_tracing(
    endpoint: str | None,
    *,
    service_name: str,
    exporter: SpanExporter | None = None,
    set_global: bool = True,
) -> TracerProvider | None:
    """Set up tracing and return the provider, or ``None`` when no endpoint is set (Req 13.1).

    ``exporter`` replaces the OTLP exporter (tests); it is still wrapped in
    :class:`BoundedSpanExporter` and the bounded ``BatchSpanProcessor`` (Req 13.2). A blank
    ``endpoint`` with no ``exporter`` builds nothing. ``set_global`` installs the provider as the
    process-wide one so ``trace.get_tracer`` works in the use cases and the middleware.
    """
    has_endpoint = endpoint is not None and endpoint.strip() != ""
    if not has_endpoint and exporter is None:
        return None
    if exporter is None:
        assert endpoint is not None
        exporter = _build_otlp_exporter(endpoint)

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(_batch_processor(BoundedSpanExporter(exporter)))
    if set_global:
        trace.set_tracer_provider(provider)
    return provider


def configure_tracing_from_env(
    environ: Mapping[str, str], *, service_name: str, set_global: bool = True
) -> TracerProvider | None:
    """:func:`configure_tracing` with the endpoint read from ``OTEL_EXPORTER_OTLP_ENDPOINT``."""
    return configure_tracing(
        environ.get(OTLP_ENDPOINT_ENV), service_name=service_name, set_global=set_global
    )


def shutdown_tracing(provider: TracerProvider | None) -> None:
    """Flush the queue and stop the export thread; ``None`` is a no-op."""
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception as error:  # shutdown must not block process exit
        _LOG.debug("tracer provider shutdown raised %s", type(error).__name__)


def _build_otlp_exporter(endpoint: str) -> SpanExporter:
    # Imported here so a process without a collector endpoint never loads the HTTP stack.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(
        endpoint=traces_url(endpoint),
        timeout=config.TRACE_EXPORT_ATTEMPT_TIMEOUT.total_seconds(),
    )


def _batch_processor(exporter: SpanExporter) -> SpanProcessor:
    return BatchSpanProcessor(
        exporter,
        max_queue_size=config.TRACE_QUEUE_MAX_SPANS,
        schedule_delay_millis=config.TRACE_EXPORT_SCHEDULE_DELAY.total_seconds() * 1000,
        max_export_batch_size=config.TRACE_EXPORT_BATCH_MAX_SPANS,
    )
