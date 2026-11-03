"""Property 1: Structured output is validated, unmodified and bounded.

For any scripted sequence of raw model responses and any ``max_retries`` in 0 to 2,
``LLMEngine.complete`` returns a result iff one of the first ``1 + max_retries``
responses passes the schema; the returned mapping equals the parse of that accepted
response with nothing rewritten; transport calls never exceed 3; and a
``max_retries`` outside 0 to 2 raises ``ValueError`` before any call.

**Validates: Requirements 1.2, 1.3, 1.5**
"""

from __future__ import annotations

import json

import pytest
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    LLMInput,
    MalformedOutputError,
    PromptRef,
)
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.transport import RawCompletion, TransportRequest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
HOST = "eu.example.test"
KEY = "key-1"


class Out(BaseModel):
    title: str
    count: int


SCHEMA = PydanticOutputSchema(Out)

_valid_payloads = st.fixed_dictionaries(
    {"title": st.text(max_size=30), "count": st.integers(-(10**6), 10**6)}
)
_invalid_texts = st.sampled_from(
    [
        "",
        "nope",
        "[]",
        "{}",
        '{"title": "x"}',
        '{"title": null, "count": 1}',
        '{"title": "x", "count": "many"}',
        '{"title": 1, "count": 1}',
    ]
)
# Each scripted response is (text, expected_payload_or_None).
_responses = st.one_of(
    _valid_payloads.map(lambda p: (json.dumps(p), p)),
    _invalid_texts.map(lambda t: (t, None)),
)


class _Transport:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    def complete(self, request: TransportRequest) -> RawCompletion:
        text = self.texts[self.calls]
        self.calls += 1
        return RawCompletion(text, "m", 1, 1, 1, origin_host=HOST, credential_id=KEY)


class _Gate:
    def before_attempt(self, attempt: int) -> None:
        return None


class _Sink:
    def record(self, usage: AttemptUsage) -> None:
        return None


def _engine(transport: _Transport) -> LLMEngine:
    return LLMEngine(
        transport,
        model="m",
        gate=_Gate(),
        sink=_Sink(),
        binding=ProviderBinding(frozenset({HOST}), KEY),
    )


@given(
    responses=st.lists(_responses, min_size=3, max_size=3),
    max_retries=st.integers(0, 2),
)
def test_structured_output_validated_unmodified_and_bounded(
    responses: list[tuple[str, dict[str, object] | None]], max_retries: int
) -> None:
    transport = _Transport([text for text, _ in responses])
    engine = _engine(transport)
    window = responses[: 1 + max_retries]
    first_valid = next((i for i, (_, p) in enumerate(window) if p is not None), None)

    if first_valid is None:
        with pytest.raises(MalformedOutputError):
            engine.complete(PROMPT, INPUT, SCHEMA, max_retries)
        assert transport.calls == 1 + max_retries
    else:
        result = engine.complete(PROMPT, INPUT, SCHEMA, max_retries)
        assert dict(result.output) == window[first_valid][1]
        assert transport.calls == first_valid + 1
    assert transport.calls <= 3


@given(max_retries=st.one_of(st.integers(max_value=-1), st.integers(min_value=3)))
def test_out_of_range_max_retries_raises_before_any_call(max_retries: int) -> None:
    transport = _Transport(['{"title": "x", "count": 1}'] * 3)
    with pytest.raises(ValueError):
        _engine(transport).complete(PROMPT, INPUT, SCHEMA, max_retries)
    assert transport.calls == 0
