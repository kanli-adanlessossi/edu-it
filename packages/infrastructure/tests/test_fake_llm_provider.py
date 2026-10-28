"""Unit tests for the in-memory Fake_LLMProvider (task 5.3, Req 5.2, 5.4).

Example and edge-case coverage: the fake satisfies the domain ``LLMProvider``
port, ``complete`` returns a deterministic completion with provenance and makes
no network call, canned responses and stop sequences are honored, and calls are
recorded for inspection. The formal substitutability and no-network assertions
are task 5.4.
"""

from __future__ import annotations

from edu_it_domain.ports import LLMCompletion, LLMProvider
from edu_it_infrastructure.fakes import Fake_LLMProvider


def test_llm_provider_satisfies_the_domain_port() -> None:
    # Substitutable for a future production adapter without changing domain code (Req 5.2).
    assert isinstance(Fake_LLMProvider(), LLMProvider)


def test_complete_returns_a_completion_with_provenance() -> None:
    provider = Fake_LLMProvider()
    result = provider.complete("Write a greeting", model="fake-model")
    assert isinstance(result, LLMCompletion)
    assert result.model == "fake-model"
    assert result.text
    assert result.prompt_tokens > 0
    assert result.cost == 0.0
    assert result.metadata["provider"] == "fake"


def test_complete_is_deterministic_for_the_same_prompt() -> None:
    provider = Fake_LLMProvider()
    first = provider.complete("same", model="m")
    second = provider.complete("same", model="m")
    assert first.text == second.text


def test_canned_response_is_returned_when_seeded() -> None:
    provider = Fake_LLMProvider(responses={"ping": "pong"})
    assert provider.complete("ping", model="m").text == "pong"


def test_stop_sequence_truncates_the_text() -> None:
    provider = Fake_LLMProvider(responses={"p": "hello|world"})
    assert provider.complete("p", model="m", stop=["|"]).text == "hello"


def test_calls_are_recorded_for_inspection() -> None:
    provider = Fake_LLMProvider()
    provider.complete("a", model="m1")
    provider.complete("b", model="m2")
    assert provider.calls == (("a", "m1"), ("b", "m2"))
