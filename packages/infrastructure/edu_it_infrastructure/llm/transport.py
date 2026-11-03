"""Infrastructure-internal model transport contract (ai-content Req 1.2, 1.7 to 1.9).

A :class:`ModelTransport` performs exactly one model call and reports what came
back as a :class:`RawCompletion`. It never validates, retries or records: the
shared engine does that. ``RawCompletion`` is infrastructure-internal and never
crosses the domain port.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable

from edu_it_domain.ports.llm_provider import LLMInput, PromptRef

__all__ = [
    "ModelTransport",
    "PromptRenderer",
    "RawCompletion",
    "RenderedPrompt",
    "TransportRequest",
    "TransportTimeoutError",
]


class TransportTimeoutError(Exception):
    """The single model call did not finish within its timeout (retried by the engine)."""


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """A pinned prompt after rendering: the full text and the name of its output schema."""

    text: str
    """Full prompt text, included prompts expanded."""

    output_schema: str
    """Output schema name from the prompt's front matter."""


#: Renders a pinned prompt. Supplied by the caller (the worker's ``PromptStore``), because
#: infrastructure does not import the worker.
PromptRenderer = Callable[[PromptRef], RenderedPrompt]


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """One model call."""

    prompt: PromptRef
    input: LLMInput
    model: str
    attempt: int
    """1-based attempt number within one ``complete`` call."""

    timeout: timedelta

    rendered_prompt: RenderedPrompt | None = None
    """The rendered prompt for ``prompt``; vendor adapters that send prompt text require it."""


@dataclass(frozen=True, slots=True)
class RawCompletion:
    """Raw model text and its usage, as reported by a transport."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_cents: int
    origin_host: str | None = None
    """Host of the final response URL; ``None`` for transports with no network."""

    credential_id: str | None = None
    """Fingerprint of the credential actually sent; ``None`` when none was used."""


@runtime_checkable
class ModelTransport(Protocol):
    """Performs one model call.

    Raises :class:`TransportTimeoutError` on timeout and
    ``ProviderUnavailableError`` when the provider cannot be reached or refuses
    the call.
    """

    def complete(self, request: TransportRequest) -> RawCompletion: ...
