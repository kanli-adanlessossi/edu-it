"""The ``LLMProvider`` port with the D6 signature (ai-content Req 1.1, 1.6, 12.1).

Ahead-of-time content generation sits behind this port, never on a learner's
request path (``.kiro/steering/tech.md``). The port is exactly
``complete(prompt_version, input, schema, max_retries) -> StructuredOutput``.
Validation, bounded retries, cost recording and budget checks live in the
implementations (a shared engine in infrastructure, plus the fake), so callers
cannot bypass them. The domain imports no vendor SDK (Req 1.6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from edu_it_domain.config import LLM_MAX_RETRIES

__all__ = [
    "AttemptUsage",
    "BudgetReachedError",
    "LLMInput",
    "LLMProvider",
    "MalformedOutputError",
    "MissingFixtureError",
    "OutputSchema",
    "PersonalDataInRequestError",
    "PromptRef",
    "ProviderBindingError",
    "ProviderUnavailableError",
    "StructuredOutput",
    "validate_max_retries",
]

#: Highest accepted ``max_retries`` (D6, Req 1.3).
MAX_RETRIES_LIMIT: Final[int] = LLM_MAX_RETRIES


# --- Errors -----------------------------------------------------------------


class MalformedOutputError(Exception):
    """Every attempt produced output that failed schema validation."""


class ProviderUnavailableError(Exception):
    """The provider could not be reached or refused the call."""


class ProviderBindingError(Exception):
    """The response origin host or credential did not match the binding."""


class BudgetReachedError(Exception):
    """A budget gate stopped the call before an attempt was made."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"budget reached: {kind}")
        self.kind = kind
        """Which budget stopped the call (for example ``daily``, ``track``, ``job``)."""


class MissingFixtureError(Exception):
    """A replay run found no recorded response for the request key."""

    def __init__(self, key: str) -> None:
        super().__init__(f"missing fixture: {key}")
        self.key = key
        """The fixture key that had no recorded response."""


class PersonalDataInRequestError(Exception):
    """The request input carried personal data; no call was made."""


# --- Value objects ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptRef:
    """Pins one versioned prompt."""

    prompt_id: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class LLMInput:
    """Typed, allowlisted request fields for one call."""

    kind: str
    """The input kind whose allowlist governs ``fields``."""

    fields: Mapping[str, str] = field(default_factory=dict)
    """Allowlisted field values only."""


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    """Usage and outcome of one model attempt, including failed ones."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_cents: int
    outcome: str
    """For example ``ok``, ``invalid``, ``timeout``, ``unavailable``, ``binding``."""


@dataclass(frozen=True, slots=True)
class StructuredOutput:
    """A validated result plus provenance for every attempt."""

    output: Mapping[str, object]
    """The mapping returned by ``OutputSchema.validate``, unmodified."""

    prompt: PromptRef
    model: str
    """The model of the accepted attempt."""

    attempts: tuple[AttemptUsage, ...]
    """Usage of every attempt, in order."""


# --- Ports ------------------------------------------------------------------


@runtime_checkable
class OutputSchema(Protocol):
    """Validates raw model text into a mapping."""

    def validate(self, text: str) -> Mapping[str, object]:
        """Return the validated mapping, or raise ``ValueError`` if invalid."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """Port for ahead-of-time large-language-model content generation."""

    def complete(
        self,
        prompt_version: PromptRef,
        input: LLMInput,
        schema: OutputSchema,
        max_retries: int,
    ) -> StructuredOutput:
        """Run one validated, bounded-retry model call.

        ``max_retries`` must be in ``0..LLM_MAX_RETRIES`` (2); anything else
        raises ``ValueError``. Failures raise the domain errors defined in this
        module.
        """
        ...


def validate_max_retries(max_retries: int) -> int:
    """Return ``max_retries`` if it is within ``0..2``, else raise ``ValueError``."""
    if isinstance(max_retries, bool) or not isinstance(max_retries, int):
        raise ValueError(f"max_retries must be an int in 0..{MAX_RETRIES_LIMIT}")
    if not 0 <= max_retries <= MAX_RETRIES_LIMIT:
        raise ValueError(f"max_retries must be in 0..{MAX_RETRIES_LIMIT}, got {max_retries}")
    return max_retries
