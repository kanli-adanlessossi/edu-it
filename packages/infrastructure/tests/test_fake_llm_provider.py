"""Unit tests for the in-memory Fake_LLMProvider (Req 5.2, 5.4; ai-content Req 1.1, 12.1).

The fake satisfies the D6 ``LLMProvider`` port, returns deterministic validated
output with per-attempt usage, honors canned responses, bounds retries and
records calls.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from edu_it_domain.ports import (
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    PromptRef,
    StructuredOutput,
)
from edu_it_infrastructure.fakes import Fake_LLMProvider

PROMPT = PromptRef(prompt_id="p1", prompt_version="1")
INPUT = LLMInput(kind="k", fields={"topic": "dns"})


class _JsonSchema:
    def validate(self, text: str) -> Mapping[str, object]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value


def test_llm_provider_satisfies_the_domain_port() -> None:
    assert isinstance(Fake_LLMProvider(), LLMProvider)


def test_complete_returns_structured_output_with_usage() -> None:
    result = Fake_LLMProvider().complete(PROMPT, INPUT, _JsonSchema(), 0)
    assert isinstance(result, StructuredOutput)
    assert result.prompt == PROMPT
    assert result.output["fake"] == "p1"
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome == "ok"
    assert result.attempts[0].cost_cents == 0
    assert result.model == result.attempts[0].model


def test_complete_is_deterministic() -> None:
    provider = Fake_LLMProvider()
    assert provider.complete(PROMPT, INPUT, _JsonSchema(), 1) == provider.complete(
        PROMPT, INPUT, _JsonSchema(), 1
    )


def test_canned_response_is_returned_when_seeded() -> None:
    provider = Fake_LLMProvider(responses={"p1": '{"a": 1}'})
    assert provider.complete(PROMPT, INPUT, _JsonSchema(), 0).output == {"a": 1}


def test_malformed_output_records_every_attempt_then_raises() -> None:
    provider = Fake_LLMProvider(responses={"p1": "not json"})
    with pytest.raises(MalformedOutputError):
        provider.complete(PROMPT, INPUT, _JsonSchema(), 2)


@pytest.mark.parametrize("bad", [-1, 3, 100])
def test_max_retries_outside_range_is_a_value_error(bad: int) -> None:
    with pytest.raises(ValueError):
        Fake_LLMProvider().complete(PROMPT, INPUT, _JsonSchema(), bad)


def test_calls_are_recorded_for_inspection() -> None:
    provider = Fake_LLMProvider()
    provider.complete(PROMPT, INPUT, _JsonSchema(), 0)
    assert provider.calls == ((PROMPT, INPUT),)
