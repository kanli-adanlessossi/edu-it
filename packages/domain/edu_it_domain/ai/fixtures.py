"""Canonical fixture keys and the Fixture value (ai-content Req 2.2, 2.6).

A Fixture is the recorded request payload plus an ordered list of attempts, so
retries with different responses under one key replay in order. Encoding is
canonical JSON (sorted keys, UTF-8), so ``decode(encode(f)) == f``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from edu_it_domain.ports.llm_provider import LLMInput, PromptRef

__all__ = ["Fixture", "FixtureAttempt", "canonical_json", "decode", "encode", "fixture_key"]

_FORMAT: Final = 1


def canonical_json(value: object) -> str:
    """Serialise ``value`` with sorted keys and no insignificant whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _input_payload(value: LLMInput | Mapping[str, object]) -> object:
    if isinstance(value, LLMInput):
        return {"kind": value.kind, "fields": dict(value.fields)}
    return value


def fixture_key(prompt: PromptRef, input: LLMInput | Mapping[str, object]) -> str:
    """Return the SHA-256 hex key of ``(prompt_id, prompt_version, input)``.

    Independent of mapping key order; differs for different triples.
    """
    triple = {
        "prompt_id": prompt.prompt_id,
        "prompt_version": prompt.prompt_version,
        "input": _input_payload(input),
    }
    return hashlib.sha256(canonical_json(triple).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FixtureAttempt:
    """One recorded model attempt."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class Fixture:
    """A recorded request and its ordered attempts."""

    key: str
    prompt_id: str
    prompt_version: str
    request: Mapping[str, object]
    attempts: tuple[FixtureAttempt, ...]


def encode(fixture: Fixture) -> str:
    """Return the canonical JSON text of ``fixture``."""
    return canonical_json(
        {
            "format": _FORMAT,
            "key": fixture.key,
            "prompt_id": fixture.prompt_id,
            "prompt_version": fixture.prompt_version,
            "request": fixture.request,
            "attempts": [
                {
                    "text": a.text,
                    "model": a.model,
                    "input_tokens": a.input_tokens,
                    "output_tokens": a.output_tokens,
                }
                for a in fixture.attempts
            ],
        }
    )


def decode(text: str) -> Fixture:
    """Parse ``text`` written by ``encode``; raise ``ValueError`` if malformed."""
    try:
        data = json.loads(text)
        if data["format"] != _FORMAT:
            raise ValueError("unsupported fixture format")
        return Fixture(
            key=str(data["key"]),
            prompt_id=str(data["prompt_id"]),
            prompt_version=str(data["prompt_version"]),
            request=dict(data["request"]),
            attempts=tuple(
                FixtureAttempt(
                    text=str(a["text"]),
                    model=str(a["model"]),
                    input_tokens=int(a["input_tokens"]),
                    output_tokens=int(a["output_tokens"]),
                )
                for a in data["attempts"]
            ),
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed fixture: {exc}") from exc
