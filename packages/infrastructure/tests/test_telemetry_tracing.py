"""Unit tests for tracing with a bounded exporter (kubernetes-deployment Req 13.1, 13.2)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence

import pytest
from edu_it_domain import config
from edu_it_infrastructure.telemetry import tracing
from edu_it_infrastructure.telemetry.tracing import (
    BoundedSpanExporter,
    configure_tracing,
    configure_tracing_from_env,
    shutdown_tracing,
    traces_url,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class _ScriptedExporter(SpanExporter):
    """Returns the scripted results in order (the last one repeats) and counts calls."""

    def __init__(self, *results: SpanExportResult | Exception) -> None:
        self._results = results or (SpanExportResult.SUCCESS,)
        self.calls = 0
        self.exported = threading.Event()
        self.shut_down = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        outcome = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        self.exported.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def shutdown(self) -> None:
        self.shut_down = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _bounded(inner: SpanExporter, sleeps: list[float] | None = None) -> BoundedSpanExporter:
    record = sleeps if sleeps is not None else []
    return BoundedSpanExporter(inner, sleep=record.append)


# Claims: 13.2
def test_attempt_limit_is_three() -> None:
    assert config.TRACE_EXPORT_MAX_ATTEMPTS == 3


# Claims: 13.2
def test_failing_export_is_tried_three_times_then_dropped() -> None:
    inner = _ScriptedExporter(SpanExportResult.FAILURE)
    sleeps: list[float] = []

    result = _bounded(inner, sleeps).export(())

    assert result is SpanExportResult.FAILURE
    assert inner.calls == 3
    assert len(sleeps) == 2  # a pause between attempts, none after the last


# Claims: 13.2
def test_exception_counts_as_a_failed_attempt_and_never_propagates() -> None:
    inner = _ScriptedExporter(ConnectionError("collector down"))

    assert _bounded(inner).export(()) is SpanExportResult.FAILURE
    assert inner.calls == 3


# Claims: 13.2
def test_export_stops_at_the_first_success() -> None:
    inner = _ScriptedExporter(SpanExportResult.FAILURE, SpanExportResult.SUCCESS)
    sleeps: list[float] = []

    assert _bounded(inner, sleeps).export(()) is SpanExportResult.SUCCESS
    assert inner.calls == 2
    assert len(sleeps) == 1


# Claims: 13.2
def test_first_attempt_success_makes_one_call_and_no_pause() -> None:
    inner = _ScriptedExporter(SpanExportResult.SUCCESS)
    sleeps: list[float] = []

    assert _bounded(inner, sleeps).export(()) is SpanExportResult.SUCCESS
    assert inner.calls == 1
    assert sleeps == []


# Claims: 13.2
def test_drop_is_logged_with_a_count_only(caplog: pytest.LogCaptureFixture) -> None:
    inner = _ScriptedExporter(RuntimeError("Authorization: Bearer abc.def.ghi"))

    with caplog.at_level(logging.DEBUG, logger=tracing.__name__):
        _bounded(inner).export(())

    text = " ".join(record.getMessage() for record in caplog.records)
    assert "dropped 0 span(s) after 3" in text
    assert "Bearer" not in text
    assert "abc.def.ghi" not in text


# Claims: 13.2
def test_invalid_bounds_are_refused() -> None:
    inner = _ScriptedExporter()
    with pytest.raises(ValueError, match="max_attempts"):
        BoundedSpanExporter(inner, max_attempts=0)
    with pytest.raises(ValueError, match="backoff"):
        BoundedSpanExporter(inner, backoff_seconds=-1)


# Claims: 13.2
def test_shutdown_and_flush_delegate_and_never_raise() -> None:
    inner = _ScriptedExporter()
    bounded = _bounded(inner)
    bounded.shutdown()
    assert inner.shut_down
    assert bounded.force_flush() is True

    class _Broken(_ScriptedExporter):
        def shutdown(self) -> None:
            raise RuntimeError("boom")

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            raise RuntimeError("boom")

    broken = _bounded(_Broken())
    broken.shutdown()
    assert broken.force_flush() is False


# Claims: 13.1
@pytest.mark.parametrize("endpoint", [None, "", "   "])
def test_no_endpoint_builds_nothing(endpoint: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(_endpoint: str) -> SpanExporter:
        raise AssertionError("no exporter may be built without an endpoint")

    monkeypatch.setattr(tracing, "_build_otlp_exporter", _fail)
    threads_before = threading.active_count()

    assert configure_tracing(endpoint, service_name="api", set_global=False) is None
    assert configure_tracing_from_env({}, service_name="api", set_global=False) is None
    assert (
        configure_tracing_from_env(
            {tracing.OTLP_ENDPOINT_ENV: ""}, service_name="api", set_global=False
        )
        is None
    )
    assert threading.active_count() == threads_before
    shutdown_tracing(None)  # a no-op


# Claims: 13.1
def test_endpoint_builds_the_otlp_exporter_for_that_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    inner = _ScriptedExporter()

    def _build(endpoint: str) -> SpanExporter:
        seen.append(endpoint)
        return inner

    monkeypatch.setattr(tracing, "_build_otlp_exporter", _build)

    provider = configure_tracing_from_env(
        {tracing.OTLP_ENDPOINT_ENV: "http://collector:4318"},
        service_name="worker",
        set_global=False,
    )

    assert provider is not None
    assert seen == ["http://collector:4318"]
    assert provider.resource.attributes["service.name"] == "worker"
    shutdown_tracing(provider)
    assert inner.shut_down


# Claims: 13.1
def test_real_otlp_exporter_targets_the_traces_url() -> None:
    exporter = tracing._build_otlp_exporter("http://collector:4318/")
    try:
        assert exporter._endpoint == "http://collector:4318/v1/traces"  # type: ignore[attr-defined]
    finally:
        exporter.shutdown()


# Claims: 13.1
@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://c:4318", "http://c:4318/v1/traces"),
        ("http://c:4318/", "http://c:4318/v1/traces"),
        (" http://c:4318/ ", "http://c:4318/v1/traces"),
        ("http://c:4318/v1/traces", "http://c:4318/v1/traces"),
    ],
)
def test_traces_url_appends_the_signal_path_once(endpoint: str, expected: str) -> None:
    assert traces_url(endpoint) == expected


# Claims: 13.2
def test_spans_are_exported_on_a_background_thread_and_ending_a_span_does_not_wait() -> None:
    export_threads: list[str] = []
    gate = threading.Event()

    class _Slow(_ScriptedExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            export_threads.append(threading.current_thread().name)
            gate.wait(timeout=5)  # a slow collector
            return super().export(spans)

    inner = _Slow()
    provider = configure_tracing(None, service_name="api", exporter=inner, set_global=False)
    assert provider is not None
    tracer = provider.get_tracer("test")
    try:
        started = time.perf_counter()
        for _ in range(20):
            with tracer.start_as_current_span("request"):
                pass
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0  # the slow exporter never blocks the caller
        assert inner.calls == 0  # nothing is exported inline when a span ends
    finally:
        gate.set()
        shutdown_tracing(provider)

    assert inner.calls >= 1
    assert export_threads and all(name != "MainThread" for name in export_threads)
    assert all("Batch" in name for name in export_threads)


# Claims: 13.2
def test_unreachable_collector_drops_spans_and_keeps_the_caller_fast() -> None:
    inner = _ScriptedExporter(SpanExportResult.FAILURE)
    provider = configure_tracing(None, service_name="api", exporter=inner, set_global=False)
    assert provider is not None
    tracer = provider.get_tracer("test")

    started = time.perf_counter()
    for _ in range(10):
        with tracer.start_as_current_span("job"):
            pass
    elapsed = time.perf_counter() - started
    shutdown_tracing(provider)

    assert elapsed < 0.05 + 0.5  # far below the 50 ms budget of Req 13.2 plus scheduling noise
    assert inner.calls % config.TRACE_EXPORT_MAX_ATTEMPTS == 0
    assert inner.calls >= config.TRACE_EXPORT_MAX_ATTEMPTS


# Claims: 13.2
def test_real_otlp_exporter_against_a_closed_port_drops_without_raising() -> None:
    provider = configure_tracing("http://127.0.0.1:9", service_name="api", set_global=False)
    assert provider is not None
    tracer = provider.get_tracer("test")

    started = time.perf_counter()
    with tracer.start_as_current_span("request"):
        pass
    elapsed = time.perf_counter() - started
    shutdown_tracing(provider)  # drains and drops; must return

    assert elapsed < 0.5


# Claims: 13.2
def test_queue_is_bounded_by_the_domain_config() -> None:
    provider = configure_tracing(
        None, service_name="api", exporter=_ScriptedExporter(), set_global=False
    )
    assert provider is not None
    try:
        processor = provider._active_span_processor._span_processors[0]  # type: ignore[attr-defined]
        batch = processor._batch_processor
        assert batch._max_queue_size == config.TRACE_QUEUE_MAX_SPANS
        assert batch._max_export_batch_size == config.TRACE_EXPORT_BATCH_MAX_SPANS
        assert batch._queue.maxlen == config.TRACE_QUEUE_MAX_SPANS
    finally:
        shutdown_tracing(provider)
