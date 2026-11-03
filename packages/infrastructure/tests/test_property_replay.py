"""Property 5: Replay returns what was recorded and fails clearly on a miss.

**Validates: Requirements 2.3, 2.4**

For any set of recorded Fixtures, a request whose key matches returns the
recorded attempts in order through the engine; for any request whose key is
absent, replay raises ``MissingFixtureError`` whose message contains the key,
having opened no network connection.
"""

from __future__ import annotations

import json
import socket
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from edu_it_domain.ai.fixtures import FixtureAttempt, fixture_key
from edu_it_domain.ports.llm_provider import LLMInput, MissingFixtureError, PromptRef
from edu_it_infrastructure.llm.engine import LLMEngine
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.replay import ReplayTransport
from edu_it_infrastructure.llm.transport import TransportRequest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")

_word = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=20)
_inputs = st.builds(
    lambda fields: LLMInput(kind="brief", fields=fields),
    st.dictionaries(st.sampled_from(["track", "brief", "locale"]), _word, min_size=1),
)
_texts = st.lists(_word, min_size=1, max_size=4)


class _Out(BaseModel):
    title: str


class _Gate:
    def before_attempt(self, attempt: int) -> None:
        return None


class _Sink:
    def record(self, usage: object) -> None:
        return None


def _attempt(text: str) -> FixtureAttempt:
    return FixtureAttempt(text=text, model="m", input_tokens=1, output_tokens=1)


def _request(input: LLMInput, n: int) -> TransportRequest:
    return TransportRequest(PROMPT, input, "m", n, timedelta(seconds=5))


@settings(max_examples=50, deadline=None)
@given(recorded=st.lists(_inputs, min_size=1, max_size=4), texts=_texts, data=st.data())
def test_matching_key_replays_recorded_attempts_in_order(
    recorded: list[LLMInput], texts: list[str], data: st.DataObject
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = FixtureStore(Path(tmp))
        for input in recorded:
            for text in texts:
                store.record_attempt(PROMPT, input, _attempt(text))
        chosen = data.draw(st.sampled_from(recorded))
        transport = ReplayTransport(store)
        # Attempts come back in recorded order; the first is the first recorded.
        replayed = [transport.complete(_request(chosen, n)).text for n in range(1, len(texts) + 1)]
        assert replayed == texts
        # Through the engine, a valid first recorded attempt is returned as recorded.
        valid = _attempt(json.dumps({"title": texts[0]}))
        engine_input = LLMInput(kind="brief", fields={"track": "engine"})
        store.record_attempt(PROMPT, engine_input, valid)
        engine = LLMEngine(transport, model="m", gate=_Gate(), sink=_Sink())
        result = engine.complete(PROMPT, engine_input, PydanticOutputSchema(_Out), 1)
        assert result.output == {"title": texts[0]}


@settings(max_examples=50, deadline=None)
@given(recorded=st.lists(_inputs, max_size=4), absent=_inputs, texts=_texts)
def test_absent_key_raises_missing_fixture_naming_key_and_opens_no_socket(
    recorded: list[LLMInput], absent: LLMInput, texts: list[str]
) -> None:
    assume(all(fixture_key(PROMPT, r) != fixture_key(PROMPT, absent) for r in recorded))
    opened: list[str] = []
    original_init = socket.socket.__init__
    original_connect = socket.socket.connect

    def spy_init(self: socket.socket, *a: Any, **k: Any) -> None:
        opened.append("socket")
        original_init(self, *a, **k)

    def spy_connect(self: socket.socket, *a: Any, **k: Any) -> None:
        opened.append("connect")
        original_connect(self, *a, **k)

    with tempfile.TemporaryDirectory() as tmp:
        store = FixtureStore(Path(tmp))
        for input in recorded:
            for text in texts:
                store.record_attempt(PROMPT, input, _attempt(text))
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(socket.socket, "__init__", spy_init)
            mp.setattr(socket.socket, "connect", spy_connect)
            with pytest.raises(MissingFixtureError) as err:
                ReplayTransport(store).complete(_request(absent, 1))
        finally:
            mp.undo()
    key = fixture_key(PROMPT, absent)
    assert key in str(err.value)
    assert err.value.key == key
    assert opened == []
