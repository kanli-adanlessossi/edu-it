"""Property 4: Fixture keys are deterministic and Fixtures round-trip.

**Validates: Requirements 2.2, 2.6**

For any ``prompt_id``, ``prompt_version`` and nested ``input``, the Fixture key is
unchanged by mapping key order and differs for different triples; for any Fixture,
``decode(encode(f)) == f`` and what ``FixtureStore`` wrote reads back equal.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from edu_it_domain.ai.fixtures import Fixture, FixtureAttempt, decode, encode, fixture_key
from edu_it_domain.ports.llm_provider import LLMInput, PromptRef
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from hypothesis import given, settings
from hypothesis import strategies as st

_names = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
_text = st.text(max_size=20)
_json = st.recursive(
    st.none() | st.booleans() | st.integers(-1000, 1000) | _text,
    lambda children: st.lists(children, max_size=3) | st.dictionaries(_text, children, max_size=3),
    max_leaves=8,
)
_nested_input = st.dictionaries(_text, _json, max_size=4)
_prompts = st.builds(PromptRef, prompt_id=_names, prompt_version=_names)
_attempts = st.builds(
    FixtureAttempt,
    text=_text,
    model=_text,
    input_tokens=st.integers(0, 10_000),
    output_tokens=st.integers(0, 10_000),
)


def _reordered(value: object) -> object:
    """Return ``value`` with every mapping rebuilt in reversed key order."""
    if isinstance(value, dict):
        return {k: _reordered(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reordered(v) for v in value]
    return value


@settings(max_examples=100)
@given(prompt=_prompts, payload=_nested_input)
def test_key_is_independent_of_mapping_key_order(
    prompt: PromptRef, payload: dict[str, object]
) -> None:
    reordered = _reordered(payload)
    assert isinstance(reordered, dict)
    assert fixture_key(prompt, payload) == fixture_key(prompt, reordered)
    assert fixture_key(prompt, payload) == fixture_key(prompt, payload)


@settings(max_examples=100)
@given(
    a=st.tuples(_prompts, _nested_input),
    b=st.tuples(_prompts, _nested_input),
)
def test_different_triples_have_different_keys(
    a: tuple[PromptRef, dict[str, object]], b: tuple[PromptRef, dict[str, object]]
) -> None:
    same = a[0] == b[0] and _reordered(a[1]) == _reordered(b[1]) and a[1] == b[1]
    ka, kb = fixture_key(*a), fixture_key(*b)
    assert (ka == kb) == same


@settings(max_examples=100)
@given(
    prompt=_prompts,
    request=_nested_input,
    attempts=st.lists(_attempts, max_size=4),
)
def test_decode_of_encode_is_identity(
    prompt: PromptRef, request: dict[str, object], attempts: list[FixtureAttempt]
) -> None:
    fixture = Fixture(
        key=fixture_key(prompt, request),
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        request=request,
        attempts=tuple(attempts),
    )
    assert decode(encode(fixture)) == fixture


@settings(max_examples=50)
@given(
    prompt=_prompts,
    fields=st.dictionaries(
        _names, st.from_regex(r"[a-z]{1,12}", fullmatch=True), min_size=1, max_size=3
    ),
    attempts=st.lists(_attempts, min_size=1, max_size=3),
)
def test_store_write_then_read_returns_equal_fixture(
    prompt: PromptRef, fields: dict[str, str], attempts: list[FixtureAttempt]
) -> None:
    llm_input = LLMInput(kind="brief", fields=fields)
    fixture = Fixture(
        key=fixture_key(prompt, llm_input),
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        request={"kind": llm_input.kind, "fields": dict(fields)},
        attempts=tuple(attempts),
    )
    with TemporaryDirectory() as tmp:
        store = FixtureStore(Path(tmp))
        store.write(fixture)
        assert store.read(prompt, llm_input) == fixture
