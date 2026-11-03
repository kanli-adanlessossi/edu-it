"""Tests for the shared LLM engine (ai-content Req 1.2 to 1.5, 1.8, 1.9, 10.4, 10.5).

**Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 10.4, 10.5**
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    PersonalDataInRequestError,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
)
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.transport import (
    RawCompletion,
    RenderedPrompt,
    TransportRequest,
    TransportTimeoutError,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
HOST = "eu.example.test"
BINDING = ProviderBinding(frozenset({HOST}), "key-1")


class Out(BaseModel):
    title: str


SCHEMA = PydanticOutputSchema(Out)


def raw(
    text: str = '{"title": "x"}', *, host: str | None = HOST, key: str | None = "key-1"
) -> RawCompletion:
    return RawCompletion(text, "m", 10, 5, 3, origin_host=host, credential_id=key)


class ScriptedTransport:
    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.requests: list[TransportRequest] = []

    def complete(self, request: TransportRequest) -> RawCompletion:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, RawCompletion)
        return step


class Gate:
    def __init__(self, stop_at: int | None = None) -> None:
        self.stop_at = stop_at
        self.calls: list[int] = []

    def before_attempt(self, attempt: int) -> None:
        self.calls.append(attempt)
        if attempt == self.stop_at:
            raise BudgetReachedError("daily")


class Sink:
    def __init__(self) -> None:
        self.usages: list[AttemptUsage] = []

    def record(self, usage: AttemptUsage) -> None:
        self.usages.append(usage)


def engine(
    transport: ScriptedTransport,
    gate: Gate | None = None,
    sink: Sink | None = None,
    binding: ProviderBinding | None = BINDING,
) -> tuple[LLMEngine, Gate, Sink]:
    gate = gate or Gate()
    sink = sink or Sink()
    return LLMEngine(transport, model="m", gate=gate, sink=sink, binding=binding), gate, sink


def test_engine_is_an_llm_provider() -> None:
    assert isinstance(engine(ScriptedTransport())[0], LLMProvider)


def test_valid_output_returned_exactly_and_recorded() -> None:
    eng, gate, sink = engine(ScriptedTransport(raw()))
    result = eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert dict(result.output) == {"title": "x"}
    assert [u.outcome for u in sink.usages] == ["ok"]
    assert result.attempts == tuple(sink.usages)
    assert gate.calls == [1]


def test_invalid_then_valid_retries_and_records_every_attempt() -> None:
    eng, gate, sink = engine(ScriptedTransport(raw("nope"), raw()))
    result = eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert [u.outcome for u in sink.usages] == ["invalid", "ok"]
    assert gate.calls == [1, 2]
    assert len(result.attempts) == 2


def test_malformed_after_three_attempts() -> None:
    t = ScriptedTransport(raw("a"), raw("b"), raw("c"))
    eng, _, sink = engine(t)
    with pytest.raises(MalformedOutputError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert len(t.requests) == 3
    assert len(sink.usages) == 3


def test_timeouts_are_retried_then_unavailable() -> None:
    t = ScriptedTransport(*(TransportTimeoutError() for _ in range(3)))
    eng, _, sink = engine(t)
    with pytest.raises(ProviderUnavailableError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert [u.outcome for u in sink.usages] == ["timeout"] * 3


def test_unavailable_is_not_retried() -> None:
    t = ScriptedTransport(ProviderUnavailableError("down"), raw())
    eng, _, sink = engine(t)
    with pytest.raises(ProviderUnavailableError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert len(t.requests) == 1
    assert [u.outcome for u in sink.usages] == ["unavailable"]


@pytest.mark.parametrize("bad", [raw(host="evil.test"), raw(key="other"), raw(host=None)])
def test_binding_failure_rejected_even_when_body_valid_and_not_retried(
    bad: RawCompletion,
) -> None:
    t = ScriptedTransport(bad, raw())
    eng, _, sink = engine(t)
    with pytest.raises(ProviderBindingError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert len(t.requests) == 1
    assert [u.outcome for u in sink.usages] == ["binding"]
    assert sink.usages[0].cost_cents == 3


def test_budget_stop_before_retry_is_not_retried_and_keeps_prior_records() -> None:
    t = ScriptedTransport(raw("bad"), raw())
    eng, _, sink = engine(t, gate=Gate(stop_at=2))
    with pytest.raises(BudgetReachedError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert len(t.requests) == 1
    assert [u.outcome for u in sink.usages] == ["invalid"]


def test_budget_stop_on_first_attempt_makes_no_call() -> None:
    t = ScriptedTransport(raw())
    eng, _, sink = engine(t, gate=Gate(stop_at=1))
    with pytest.raises(BudgetReachedError):
        eng.complete(PROMPT, INPUT, SCHEMA, 2)
    assert t.requests == [] and sink.usages == []


def test_personal_data_blocks_before_gate_and_call() -> None:
    t = ScriptedTransport(raw())
    eng, gate, sink = engine(t)
    bad = LLMInput(kind="brief", fields={"track": "someone@example.com"})
    with pytest.raises(PersonalDataInRequestError):
        eng.complete(PROMPT, bad, SCHEMA, 2)
    assert t.requests == [] and gate.calls == [] and sink.usages == []


@pytest.mark.parametrize("retries", [-1, 3])
def test_max_retries_out_of_range(retries: int) -> None:
    with pytest.raises(ValueError):
        engine(ScriptedTransport())[0].complete(PROMPT, INPUT, SCHEMA, retries)


def test_no_binding_for_networkless_transport() -> None:
    eng, _, _ = engine(ScriptedTransport(raw(host=None, key=None)), binding=None)
    assert eng.complete(PROMPT, INPUT, SCHEMA, 0).model == "m"


def test_pydantic_schema_rejects_bad_json() -> None:
    with pytest.raises(ValueError):
        SCHEMA.validate("{")
    result: Mapping[str, object] = SCHEMA.validate('{"title": "a"}')
    assert result == {"title": "a"}


@given(retries=st.integers(0, 2), fail_count=st.integers(0, 3))
def test_property_attempts_bounded_and_all_recorded(retries: int, fail_count: int) -> None:
    """**Validates: Requirements 1.3, 1.4**"""
    steps = [raw("bad")] * fail_count + [raw()] * 3
    t = ScriptedTransport(*steps)
    eng, gate, sink = engine(t)
    try:
        result = eng.complete(PROMPT, INPUT, SCHEMA, retries)
        assert len(result.attempts) == fail_count + 1
    except MalformedOutputError:
        assert fail_count >= retries + 1
    assert len(t.requests) <= retries + 1
    assert len(sink.usages) == len(t.requests) == len(gate.calls)


def test_rendered_prompt_reaches_the_transport_on_every_attempt() -> None:
    """Validates: Requirements 1.7, 1.10"""
    rendered = RenderedPrompt(text="PROMPT TEXT", output_schema="Out")
    seen: list[PromptRef] = []

    def render(ref: PromptRef) -> RenderedPrompt:
        seen.append(ref)
        return rendered

    transport = ScriptedTransport(raw("not json"), raw())
    eng = LLMEngine(
        transport, model="m", gate=Gate(), sink=Sink(), binding=BINDING, prompt_renderer=render
    )

    eng.complete(PROMPT, INPUT, SCHEMA, 1)

    assert seen == [PROMPT]  # rendered once per complete call
    assert [r.rendered_prompt for r in transport.requests] == [rendered, rendered]
