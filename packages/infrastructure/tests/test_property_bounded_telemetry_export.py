"""Feature: kubernetes-deployment, Property 21: Telemetry export is bounded (task 10.5).

**Validates: Requirements 13.2**
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from edu_it_infrastructure.telemetry.tracing import BoundedSpanExporter
from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class _ScriptedExporter(SpanExporter):
    """An inner exporter whose ``export`` outcome for each call is fixed by a script.

    Each entry is ``True`` (succeed), ``False`` (return ``FAILURE``) or ``"raise"`` (raise an
    exception). Every call is counted regardless of outcome.
    """

    def __init__(self, outcomes: list[bool | str]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.calls += 1
        outcome = self._outcomes[self.calls - 1]
        if outcome == "raise":
            raise RuntimeError("collector unreachable")
        return SpanExportResult.SUCCESS if outcome else SpanExportResult.FAILURE

    def shutdown(self) -> None:  # pragma: no cover - not exercised here
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # pragma: no cover
        return True


def _spans(count: int) -> Sequence[ReadableSpan]:
    """A sequence of ``count`` placeholder spans (only ever counted, never inspected)."""
    return [cast("ReadableSpan", object()) for _ in range(count)]


# A failure script has at most 3 entries (max_attempts) all failing or raising; a success script
# has a success somewhere within the first 3 entries, with failures/raises before it.
_failure_outcomes = st.lists(st.sampled_from([False, "raise"]), min_size=1, max_size=3)
_success_at = st.integers(min_value=1, max_value=3)


# Feature: kubernetes-deployment, Property 21: Telemetry export is bounded
# Claims: 13.2
# **Validates: Requirements 13.2**
@settings(max_examples=100, deadline=None)
@given(outcomes=_failure_outcomes)
def test_failing_batch_is_tried_at_most_max_attempts_then_dropped(
    outcomes: list[bool | str],
) -> None:
    # Pad the script so the exporter never runs out of scripted outcomes even if it tried more
    # than max_attempts times (which would itself be a bug this test should catch).
    padded = outcomes + [False] * 5
    inner = _ScriptedExporter(padded)
    exporter = BoundedSpanExporter(inner, max_attempts=3, backoff_seconds=0.0, sleep=lambda _: None)

    result = exporter.export(_spans(0))

    assert result is SpanExportResult.FAILURE
    assert inner.calls <= 3


@settings(max_examples=100, deadline=None)
@given(success_at=_success_at, data=st.data())
def test_batch_that_eventually_succeeds_within_max_attempts_reports_success(
    success_at: int, data: st.DataObject
) -> None:
    failures_before = data.draw(
        st.lists(
            st.sampled_from([False, "raise"]), min_size=success_at - 1, max_size=success_at - 1
        )
    )
    script: list[bool | str] = [*cast("list[bool | str]", failures_before), True]
    inner = _ScriptedExporter(script)
    exporter = BoundedSpanExporter(inner, max_attempts=3, backoff_seconds=0.0, sleep=lambda _: None)

    result = exporter.export(_spans(0))

    assert result is SpanExportResult.SUCCESS
    assert inner.calls == success_at
    assert inner.calls <= 3


@settings(max_examples=50, deadline=None)
@given(count=st.integers(min_value=0, max_value=5))
def test_export_never_raises_even_when_inner_always_raises(count: int) -> None:
    inner = _ScriptedExporter(["raise", "raise", "raise"])
    exporter = BoundedSpanExporter(inner, max_attempts=3, backoff_seconds=0.0, sleep=lambda _: None)

    result = exporter.export(_spans(count))  # must not raise

    assert result is SpanExportResult.FAILURE
    assert inner.calls == 3


# Claims: 13.2
@settings(max_examples=10, deadline=None)
@given(
    values=st.lists(st.integers(min_value=-1000, max_value=1000), max_size=5),
    mode=st.sampled_from([False, "raise"]),
)
def test_traced_operation_result_is_unchanged_by_a_failing_exporter(
    values: list[int], mode: bool | str
) -> None:
    from edu_it_infrastructure.telemetry.tracing import configure_tracing, shutdown_tracing
    from opentelemetry.trace import Tracer

    def operation(tracer: Tracer) -> int:
        total = 0
        for value in values:
            with tracer.start_as_current_span("op"):
                total += value
        return total

    class _AlwaysFailing(_ScriptedExporter):
        def __init__(self) -> None:
            super().__init__([mode] * 1000)

    failing = _AlwaysFailing()
    provider = configure_tracing(None, service_name="test", exporter=failing, set_global=False)
    assert provider is not None
    try:
        traced = operation(provider.get_tracer("test"))
    finally:
        shutdown_tracing(provider)

    assert traced == sum(values)
    # one batch is attempted at most 3 times (batches hold up to 512 spans; here <= 5 spans)
    assert failing.calls <= 3
