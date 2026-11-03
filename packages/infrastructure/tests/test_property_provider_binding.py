"""Property 3: Responses outside the configured binding are rejected.

**Validates: Requirements 1.8, 1.9**

For any response whose origin host or credential fingerprint differs from the
configured ``ProviderBinding``, including one whose body passes the schema, the
call fails with ``ProviderBindingError`` and returns no output; for any
response whose origin matches, the binding does not cause a rejection.
"""

from __future__ import annotations

from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    LLMInput,
    PromptRef,
    ProviderBindingError,
)
from edu_it_infrastructure.llm.engine import LLMEngine, ProviderBinding
from edu_it_infrastructure.llm.pydantic_schema import PydanticOutputSchema
from edu_it_infrastructure.llm.transport import RawCompletion, TransportRequest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

PROMPT = PromptRef(prompt_id="brief", prompt_version="1")
INPUT = LLMInput(kind="brief", fields={"track": "networking"})
VALID_BODY = '{"title": "x"}'

_host = st.from_regex(r"[a-z][a-z0-9]{0,8}(\.[a-z][a-z0-9]{0,8}){1,2}", fullmatch=True)
_key = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=12)


class Out(BaseModel):
    title: str


class OneShotTransport:
    def __init__(self, completion: RawCompletion) -> None:
        self.completion = completion
        self.calls = 0

    def complete(self, request: TransportRequest) -> RawCompletion:
        self.calls += 1
        return self.completion


class OpenGate:
    def before_attempt(self, attempt: int) -> None:
        return None


class Sink:
    def __init__(self) -> None:
        self.usages: list[AttemptUsage] = []

    def record(self, usage: AttemptUsage) -> None:
        self.usages.append(usage)


def _completion(host: str | None, key: str | None) -> RawCompletion:
    return RawCompletion(VALID_BODY, "m", 10, 5, 3, origin_host=host, credential_id=key)


def _run(
    binding: ProviderBinding, completion: RawCompletion
) -> tuple[object, Sink, OneShotTransport]:
    transport = OneShotTransport(completion)
    sink = Sink()
    engine = LLMEngine(transport, model="m", gate=OpenGate(), sink=sink, binding=binding)
    schema = PydanticOutputSchema(Out)
    try:
        result: object = engine.complete(PROMPT, INPUT, schema, 2)
    except ProviderBindingError as exc:
        result = exc
    return result, sink, transport


@given(
    allowed=st.frozensets(_host, min_size=1, max_size=3),
    other_host=_host,
    credential=_key,
    other_key=_key,
    mismatch=st.sampled_from(["host", "key", "both", "no_host", "no_key"]),
)
def test_mismatched_response_is_rejected_with_no_output(
    allowed: frozenset[str],
    other_host: str,
    credential: str,
    other_key: str,
    mismatch: str,
) -> None:
    binding = ProviderBinding(allowed, credential)
    good_host = sorted(allowed)[0]
    foreign_host = (
        other_host if other_host.casefold() not in {h.casefold() for h in allowed} else None
    )
    foreign_key = other_key if other_key != credential else None
    cases: dict[str, tuple[str | None, str | None] | None] = {
        "host": (foreign_host, credential) if foreign_host else None,
        "key": (good_host, foreign_key) if foreign_key else None,
        "both": (foreign_host, foreign_key) if foreign_host and foreign_key else None,
        "no_host": (None, credential),
        "no_key": (good_host, None),
    }
    pair = cases[mismatch]
    if pair is None:
        return  # drawn value collided with the binding; not a mismatch
    result, sink, transport = _run(binding, _completion(*pair))
    assert isinstance(result, ProviderBindingError)
    assert transport.calls == 1  # not retried
    assert [u.outcome for u in sink.usages] == ["binding"]


@given(
    allowed=st.frozensets(_host, min_size=1, max_size=3),
    credential=_key,
    pick=st.integers(min_value=0, max_value=2),
    upper=st.booleans(),
)
def test_matching_response_is_not_rejected(
    allowed: frozenset[str], credential: str, pick: int, upper: bool
) -> None:
    binding = ProviderBinding(allowed, credential)
    host = sorted(allowed)[pick % len(allowed)]
    host = host.upper() if upper else host
    result, sink, _ = _run(binding, _completion(host, credential))
    assert not isinstance(result, ProviderBindingError)
    assert [u.outcome for u in sink.usages] == ["ok"]
