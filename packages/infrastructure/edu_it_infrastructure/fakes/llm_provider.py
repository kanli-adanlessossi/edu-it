"""The in-memory ``LLMProvider`` fake (Req 5.2, 5.4, ADR 0007, ai-content D6).

:class:`Fake_LLMProvider` implements the domain D6
:class:`~edu_it_domain.ports.llm_provider.LLMProvider` port entirely in process.
It makes **no network call** (Req 5.4) and never returns a recorded model
response: text is a deterministic, locally computed stub, optionally seeded with
canned raw text keyed by ``prompt_id``. Like the real engine it validates the raw
text with the caller's schema, retries within ``max_retries``, and reports every
attempt in the result. It is for unit tests of callers only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    OutputSchema,
    PromptRef,
    StructuredOutput,
    validate_max_retries,
)

from edu_it_infrastructure.llm.engine import AttemptGate, UsageSink

__all__ = ["Fake_LLMProvider"]

_FAKE_MODEL = "fake-model"


class Fake_LLMProvider(LLMProvider):
    """An :class:`LLMProvider` that returns deterministic local output."""

    __test__ = False

    def __init__(
        self,
        responses: Mapping[str, str] | None = None,
        *,
        gate: AttemptGate | None = None,
        sink: UsageSink | None = None,
    ) -> None:
        """Create a provider, optionally seeded with canned raw text.

        ``responses`` maps a ``prompt_id`` to the raw text returned for it. A
        prompt with no canned text gets a deterministic JSON stub derived from
        the prompt id and input fields. ``gate`` and ``sink`` bind a budget gate
        and a usage ledger, exactly as for the real engine.
        """
        self._responses: dict[str, str] = dict(responses) if responses is not None else {}
        self._calls: list[tuple[PromptRef, LLMInput]] = []
        self._gate = gate
        self._sink = sink

    def _record(self, attempts: list[AttemptUsage], usage: AttemptUsage) -> None:
        attempts.append(usage)
        if self._sink is not None:
            self._sink.record(usage)

    def complete(
        self,
        prompt_version: PromptRef,
        input: LLMInput,
        schema: OutputSchema,
        max_retries: int,
    ) -> StructuredOutput:
        """Return validated deterministic output (no network call).

        Raises ``ValueError`` for ``max_retries`` outside ``0..2`` and
        :class:`MalformedOutputError` when every attempt fails validation.
        """
        validate_max_retries(max_retries)
        self._calls.append((prompt_version, input))
        text = self._responses.get(prompt_version.prompt_id, self._stub(prompt_version, input))
        request_tokens = self._count_tokens(" ".join(input.fields.values()))
        attempts: list[AttemptUsage] = []
        for attempt in range(1, 2 + max_retries):
            if self._gate is not None:
                self._gate.before_attempt(attempt)
            try:
                output = schema.validate(text)
            except ValueError:
                self._record(attempts, self._usage(request_tokens, text, "invalid"))
                continue
            self._record(attempts, self._usage(request_tokens, text, "ok"))
            return StructuredOutput(
                output=output,
                prompt=prompt_version,
                model=_FAKE_MODEL,
                attempts=tuple(attempts),
            )
        raise MalformedOutputError(
            f"all {len(attempts)} attempts failed validation for {prompt_version.prompt_id}"
        )

    @property
    def calls(self) -> tuple[tuple[PromptRef, LLMInput], ...]:
        """Every ``(prompt, input)`` completed so far, for test inspection."""
        return tuple(self._calls)

    @staticmethod
    def _usage(request_tokens: int, text: str, outcome: str) -> AttemptUsage:
        return AttemptUsage(
            model=_FAKE_MODEL,
            input_tokens=request_tokens,
            output_tokens=Fake_LLMProvider._count_tokens(text),
            cost_cents=0,
            outcome=outcome,
        )

    @staticmethod
    def _stub(prompt: PromptRef, input: LLMInput) -> str:
        """Deterministic JSON stub for ``prompt`` and ``input``."""
        return json.dumps(
            {
                "fake": prompt.prompt_id,
                "version": prompt.prompt_version,
                "kind": input.kind,
                "fields": dict(sorted(input.fields.items())),
            },
            sort_keys=True,
        )

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Approximate a token count by whitespace-splitting ``text``."""
        return len(text.split())
