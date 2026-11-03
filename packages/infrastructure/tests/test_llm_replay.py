"""Tests for the fixture store and replay transport (ai-content Req 2.2 to 2.4, 2.6, 3.2).

**Validates: Requirements 2.2, 2.3, 2.4, 2.6, 3.2**
"""

from __future__ import annotations

import socket
from datetime import timedelta
from pathlib import Path

import pytest
from edu_it_domain.ai.fixtures import Fixture, FixtureAttempt, decode, fixture_key
from edu_it_domain.ports.llm_provider import (
    LLMInput,
    MissingFixtureError,
    PersonalDataInRequestError,
    PromptRef,
)
from edu_it_infrastructure.llm.engine import LLMEngine
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.replay import ReplayTransport
from edu_it_infrastructure.llm.transport import ModelTransport, TransportRequest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})


def attempt(text: str = '{"title": "x"}') -> FixtureAttempt:
    return FixtureAttempt(text=text, model="m", input_tokens=4, output_tokens=2)


def request(n: int = 1, input: LLMInput = INPUT) -> TransportRequest:
    return TransportRequest(PROMPT, input, "m", n, timedelta(seconds=5))


def test_record_writes_canonical_file_at_prompt_and_key(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    store.record_attempt(PROMPT, INPUT, attempt())
    path = tmp_path / "brief" / f"{fixture_key(PROMPT, INPUT)}.json"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert decode(text).attempts == (attempt(),)


def test_attempts_for_one_key_are_ordered(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    store.record_attempt(PROMPT, INPUT, attempt("bad"))
    store.record_attempt(PROMPT, INPUT, attempt('{"title": "x"}'))
    replay = ReplayTransport(store)
    assert replay.complete(request(1)).text == "bad"
    assert replay.complete(request(2)).text == '{"title": "x"}'
    with pytest.raises(MissingFixtureError):
        replay.complete(request(3))


def test_missing_fixture_names_key_and_opens_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("socket used")

    monkeypatch.setattr(socket.socket, "connect", boom)
    with pytest.raises(MissingFixtureError) as err:
        ReplayTransport(FixtureStore(tmp_path)).complete(request())
    assert err.value.key == fixture_key(PROMPT, INPUT)
    assert err.value.key in str(err.value)


def test_write_rejects_personal_data_and_writes_nothing(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path, seeded_personal_data=["Ada Lovelace"])
    for value in ("ada@example.org", "usr_abc123", "note by Ada Lovelace"):
        bad = LLMInput(kind="brief", fields={"track": value})
        with pytest.raises(PersonalDataInRequestError):
            store.record_attempt(PROMPT, bad, attempt())
    assert list(tmp_path.rglob("*.json")) == []


def test_unsafe_prompt_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FixtureStore(tmp_path).path_for("../x", "k")


def test_replay_is_a_model_transport_and_works_through_the_engine(tmp_path: Path) -> None:
    class Out(BaseModel):
        title: str

    class Gate:
        def before_attempt(self, attempt: int) -> None:
            return None

    class Sink:
        def __init__(self) -> None:
            self.seen: list[object] = []

        def record(self, usage: object) -> None:
            self.seen.append(usage)

    store = FixtureStore(tmp_path)
    store.record_attempt(PROMPT, INPUT, attempt("not json"))
    store.record_attempt(PROMPT, INPUT, attempt())
    transport = ReplayTransport(store)
    assert isinstance(transport, ModelTransport)
    sink = Sink()
    engine = LLMEngine(transport, model="m", gate=Gate(), sink=sink)
    result = engine.complete(PROMPT, INPUT, PydanticOutputSchema(Out), 2)
    assert result.output == {"title": "x"}
    assert [a.outcome for a in result.attempts] == ["invalid", "ok"]


_text = st.text(alphabet=st.characters(categories=("L", "N", "Zs")), max_size=30)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
@given(
    fields=st.dictionaries(st.sampled_from(["track", "brief", "locale"]), _text, min_size=1),
    texts=st.lists(_text, min_size=1, max_size=3),
)
def test_write_then_read_round_trips(
    tmp_path: Path, fields: dict[str, str], texts: list[str]
) -> None:
    """Property: a written fixture reads back equivalent (Req 2.6)."""
    input = LLMInput(kind="brief", fields=fields)
    fixture = Fixture(
        key=fixture_key(PROMPT, input),
        prompt_id=PROMPT.prompt_id,
        prompt_version=PROMPT.prompt_version,
        request={"kind": input.kind, "fields": dict(fields)},
        attempts=tuple(attempt(t) for t in texts),
    )
    store = FixtureStore(tmp_path)
    try:
        store.write(fixture)
    except PersonalDataInRequestError:
        return
    assert store.read(PROMPT, input) == fixture
