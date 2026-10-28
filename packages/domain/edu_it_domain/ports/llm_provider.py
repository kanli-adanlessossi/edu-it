"""The ``LLMProvider`` port (Req 5.1, 5.4).

Ahead-of-time content generation sits behind this port, never on a learner's
request path (``.kiro/steering/tech.md``). In this phase only a fake exists:
development and test use the fake and make no network call to a real provider
(Req 5.4, ADR 0007). The port is declared now so the layering and the fake are
in place; the production adapter arrives with the learning features.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LLMCompletion:
    """The result of one completion call.

    Carries the generated text plus the provenance the platform records for
    every generation (model, token counts, cost) per ``.kiro/steering/tech.md``.
    """

    text: str
    """The generated text."""

    model: str
    """Identifier of the model that produced the completion."""

    prompt_tokens: int = 0
    """Tokens consumed by the prompt."""

    completion_tokens: int = 0
    """Tokens produced in the completion."""

    cost: float = 0.0
    """Recorded monetary cost of the call, in the provider's accounting unit."""

    metadata: Mapping[str, str] = field(default_factory=dict)
    """Optional opaque provenance metadata."""


@runtime_checkable
class LLMProvider(Protocol):
    """Port for ahead-of-time large-language-model content generation."""

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        stop: Sequence[str] | None = None,
    ) -> LLMCompletion:
        """Generate a completion for ``prompt`` using ``model``.

        ``stop`` is an optional list of stop sequences. The returned
        :class:`LLMCompletion` carries both the text and the provenance the
        platform records (model, tokens, cost).
        """
        ...
