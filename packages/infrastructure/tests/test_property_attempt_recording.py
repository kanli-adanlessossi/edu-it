"""Property 2: Every attempt is recorded (ai-content task 4.7).

**Validates: Requirements 1.4, 12.1, 12.3**

For any sequence of attempts ending in success, malformed output, timeout or
unavailability, the ledger rows equal the attempts made, each row carries that
attempt's model, tokens and cost, and the job totals equal the sum of the rows,
failed attempts included.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import pytest
from edu_it_domain.ports.generation_ledger import JobKind, NewJob
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    LLMInput,
    MalformedOutputError,
    PromptRef,
    ProviderUnavailableError,
    StructuredOutput,
)
from edu_it_infrastructure.fakes import In_Memory_GenerationLedger, Test_Clock
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.ledger_binding import (
    JobScope,
    LedgerAttemptGate,
    LedgerUsageSink,
)
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.transport import (
    RawCompletion,
    TransportRequest,
    TransportTimeoutError,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
HOST = "eu.example.test"
KEY = "key-1"
ENGINE_MODEL = "m"


class _Out(BaseModel):
    title: str


SCHEMA = PydanticOutputSchema(_Out)


@dataclass(frozen=True)
class _Step:
    """One scripted transport reaction: its kind and the usage it reports."""

    kind: str  # "ok" | "invalid" | "timeout" | "unavailable"
    model: str
    input_tokens: int
    output_tokens: int
    cost_cents: int


class _ScriptedTransport:
    def __init__(self, steps: list[_Step]) -> None:
        self._steps = list(steps)
        self.calls = 0

    def complete(self, request: TransportRequest) -> RawCompletion:
        step = self._steps[self.calls]
        self.calls += 1
        if step.kind == "timeout":
            raise TransportTimeoutError
        if step.kind == "unavailable":
            raise ProviderUnavailableError("down")
        text = '{"title": "x"}' if step.kind == "ok" else "not json"
        return RawCompletion(
            text,
            step.model,
            step.input_tokens,
            step.output_tokens,
            step.cost_cents,
            origin_host=HOST,
            credential_id=KEY,
        )


_steps = st.builds(
    _Step,
    kind=st.sampled_from(["ok", "invalid", "timeout", "unavailable"]),
    model=st.sampled_from(["m-a", "m-b", "m-c"]),
    input_tokens=st.integers(0, 5000),
    output_tokens=st.integers(0, 5000),
    cost_cents=st.integers(0, 500),
)


def _expected_usage(step: _Step) -> AttemptUsage:
    """Timeouts and unavailability report no usage (engine model); others the response's."""
    if step.kind in ("timeout", "unavailable"):
        return AttemptUsage(ENGINE_MODEL, 0, 0, 0, step.kind)
    return AttemptUsage(
        step.model, step.input_tokens, step.output_tokens, step.cost_cents, step.kind
    )


@settings(max_examples=200, deadline=None)
@given(steps=st.lists(_steps, min_size=3, max_size=3), max_retries=st.integers(0, 2))
def test_every_attempt_is_recorded(steps: list[_Step], max_retries: int) -> None:
    """**Validates: Requirements 1.4, 12.1, 12.3**"""
    clock = Test_Clock()
    ledger = In_Memory_GenerationLedger(clock)
    job = ledger.open_job(NewJob(stable_id="job-1", kind=JobKind.STAGE))
    # Limits are generous so the budget gate never interferes with recording.
    scope = JobScope(
        ledger,
        job,
        clock,
        daily_limit_cents=10**9,
        track_limit_cents=10**9,
    )
    transport = _ScriptedTransport(steps)
    engine = LLMEngine(
        transport,
        model=ENGINE_MODEL,
        gate=LedgerAttemptGate(scope),
        sink=LedgerUsageSink(scope),
        binding=ProviderBinding(frozenset({HOST}), KEY),
    )

    result: StructuredOutput | None = None
    with contextlib.suppress(MalformedOutputError, ProviderUnavailableError):
        result = engine.complete(PROMPT, INPUT, SCHEMA, max_retries)

    made = steps[: transport.calls]
    expected = [_expected_usage(s) for s in made]
    rows = [row.usage for row in ledger._jobs[job.stable_id].rows]

    # Rows equal attempts made, in order, each with its own model/tokens/cost.
    assert len(rows) == transport.calls == len(made)
    assert rows == expected
    assert [r.outcome for r in rows] == [s.kind for s in made]
    # Job totals equal the sum of the rows, failed attempts included.
    tokens, cost = ledger.job_totals(job)
    assert tokens == sum(u.input_tokens + u.output_tokens for u in expected)
    assert cost == sum(u.cost_cents for u in expected)
    assert ledger.spent_for_job(job) == cost
    if result is not None:
        assert result.attempts == tuple(rows)


def test_failed_attempts_count_toward_totals() -> None:
    """**Validates: Requirements 1.4, 12.1, 12.3**"""
    clock = Test_Clock()
    ledger = In_Memory_GenerationLedger(clock)
    job = ledger.open_job(NewJob(stable_id="job-2", kind=JobKind.STAGE))
    scope = JobScope(ledger, job, clock)
    steps = [_Step("invalid", "m-a", 10, 5, 7)] * 3
    engine = LLMEngine(
        _ScriptedTransport(steps),
        model="m",
        gate=LedgerAttemptGate(scope),
        sink=LedgerUsageSink(scope),
        binding=ProviderBinding(frozenset({HOST}), KEY),
    )
    with pytest.raises(MalformedOutputError):
        engine.complete(PROMPT, INPUT, SCHEMA, 2)
    assert len(ledger._jobs["job-2"].rows) == 3
    assert ledger.job_totals(job) == (45, 21)
