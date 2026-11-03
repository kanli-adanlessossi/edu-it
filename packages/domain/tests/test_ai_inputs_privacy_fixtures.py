"""Unit tests for ai/inputs, privacy, pseudonym and fixtures (ai-content 1.5)."""

from __future__ import annotations

import pytest
from edu_it_domain.ai.fixtures import Fixture, FixtureAttempt, decode, encode, fixture_key
from edu_it_domain.ai.inputs import INPUT_FIELDS, InputKind, disallowed_fields
from edu_it_domain.ai.privacy import ensure_clean, scan, scan_payload
from edu_it_domain.ai.pseudonym import handle
from edu_it_domain.ports.llm_provider import LLMInput, PersonalDataInRequestError, PromptRef


def test_every_kind_has_an_allowlist() -> None:
    assert set(INPUT_FIELDS) == set(InputKind)


def test_verify_answer_has_no_key_field() -> None:
    allowed = INPUT_FIELDS[InputKind.VERIFY_ANSWER]
    assert allowed == {"question", "options", "locale"}
    bad = LLMInput("verify_answer", {"question": "Q", "answer_key": "B"})
    assert disallowed_fields(bad) == ("answer_key",)
    assert scan(bad) == ("answer_key: field-not-allowed",)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        disallowed_fields(LLMInput("nope", {}))


def test_clean_input_passes() -> None:
    ensure_clean(LLMInput("verify_answer", {"question": "What does ls do?", "locale": "en"}))


@pytest.mark.parametrize(
    "text",
    [
        "mail me at jane.doe@example.com",
        "user usr_01HZX9ABC",
        "cookie sess_abcdef123456",
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
    ],
)
def test_personal_data_values_are_rejected_without_echo(text: str) -> None:
    value = LLMInput("verify_answer", {"question": text})
    with pytest.raises(PersonalDataInRequestError) as err:
        ensure_clean(value)
    assert text not in str(err.value)


def test_seeded_sample_is_found_in_nested_payload() -> None:
    findings = scan_payload({"a": [{"b": "hello Jane Doe"}]}, seeded=("jane doe",))
    assert findings == ("a[0].b: seeded-sample",)


def test_handle_is_deterministic_keyed_and_per_run() -> None:
    h = handle("k", "run1", "usr_1")
    assert h == handle(b"k", "run1", "usr_1")
    assert h != handle("k", "run2", "usr_1")
    assert h != handle("k2", "run1", "usr_1")
    assert "usr_1" not in h
    with pytest.raises(ValueError):
        handle("", "run1", "usr_1")


def test_fixture_key_ignores_key_order_and_differs_by_triple() -> None:
    p = PromptRef("quiz", "1")
    a = fixture_key(p, {"x": 1, "y": {"b": 2, "a": 3}})
    assert a == fixture_key(p, {"y": {"a": 3, "b": 2}, "x": 1})
    assert a != fixture_key(PromptRef("quiz", "2"), {"x": 1, "y": {"b": 2, "a": 3}})
    assert a != fixture_key(PromptRef("lesson", "1"), {"x": 1, "y": {"b": 2, "a": 3}})
    assert fixture_key(p, LLMInput("quiz", {"a": "1"})) == fixture_key(
        p, LLMInput("quiz", {"a": "1"})
    )


def test_fixture_round_trip() -> None:
    fx = Fixture(
        key="k",
        prompt_id="quiz",
        prompt_version="1",
        request={"kind": "quiz", "fields": {"a": "é"}},
        attempts=(FixtureAttempt("bad", "m", 1, 2), FixtureAttempt("{}", "m", 3, 4)),
    )
    assert decode(encode(fx)) == fx
    assert encode(fx) == encode(decode(encode(fx)))
    with pytest.raises(ValueError):
        decode('{"format": 1}')
