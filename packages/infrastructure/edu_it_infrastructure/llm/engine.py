"""The shared LLM engine (ai-content Req 1.2 to 1.5, 1.8, 1.9, 10.4, 10.5).

One engine serves every non-fake ``LLMProvider`` (Live and Replay), so the rules
hold for both: privacy filter first, the budget gate before *every* attempt
(retries included, no switch to disable it), a provider-binding check on every
response, schema validation, at most ``1 + max_retries`` attempts, and a usage
record for every attempt that reaches the transport.

The accepted output is returned exactly as ``schema.validate`` produced it. The
engine never edits, patches or defaults a field. Unavailability, binding
failures, configuration faults (``VendorProtocolError`` from an HTTP 400 or 404) and budget
stops are not retried; timeouts and invalid output are.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable

from edu_it_domain.ai.privacy import ensure_clean
from edu_it_domain.config import LLM_ATTEMPT_TIMEOUT
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    OutputSchema,
    PromptRef,
    ProviderBindingError,
    ProviderUnavailableError,
    StructuredOutput,
    validate_max_retries,
)

from edu_it_infrastructure.llm.registry import VendorProtocolError
from edu_it_infrastructure.llm.transport import (
    ModelTransport,
    PromptRenderer,
    RawCompletion,
    TransportRequest,
    TransportTimeoutError,
)

__all__ = ["AttemptGate", "LLMEngine", "ProviderBinding", "UsageSink"]


@runtime_checkable
class AttemptGate(Protocol):
    """Budget gate evaluated before every attempt."""

    def before_attempt(self, attempt: int) -> None:
        """Raise ``BudgetReachedError`` when no further attempt may start."""
        ...


@runtime_checkable
class UsageSink(Protocol):
    """Receives the usage of every attempt, failed ones included."""

    def record(self, usage: AttemptUsage) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    """The only origin hosts and credential a response may come from (Req 1.8, 1.9)."""

    allowed_hosts: frozenset[str]
    credential_id: str | Callable[[], str | None]
    """The expected fingerprint, or a function returning that of the key currently in use
    (a rotating credential), so a rotation is never a false binding failure."""

    def accepts(self, completion: RawCompletion) -> bool:
        host = (completion.origin_host or "").casefold()
        expected = self.credential_id() if callable(self.credential_id) else self.credential_id
        return (
            host in {h.casefold() for h in self.allowed_hosts}
            and expected is not None
            and completion.credential_id == expected
        )


class LLMEngine(LLMProvider):
    """Validate, retry and record around a :class:`ModelTransport`."""

    def __init__(
        self,
        transport: ModelTransport,
        *,
        model: str,
        gate: AttemptGate,
        sink: UsageSink,
        binding: ProviderBinding | None = None,
        seeded_personal_data: Iterable[str] = (),
        attempt_timeout: timedelta = LLM_ATTEMPT_TIMEOUT,
        prompt_renderer: PromptRenderer | None = None,
    ) -> None:
        """``binding=None`` is for transports with no network (Replay).

        ``prompt_renderer`` renders the pinned prompt once per ``complete`` call; its result
        travels on each :class:`TransportRequest` for adapters that send the prompt text.
        """
        self._renderer = prompt_renderer
        self._transport = transport
        self._model = model
        self._gate = gate
        self._sink = sink
        self._binding = binding
        self._seeded = tuple(seeded_personal_data)
        self._timeout = attempt_timeout

    def complete(
        self,
        prompt_version: PromptRef,
        input: LLMInput,
        schema: OutputSchema,
        max_retries: int,
    ) -> StructuredOutput:
        validate_max_retries(max_retries)
        ensure_clean(input, self._seeded)  # before any gate, record or call
        rendered = self._renderer(prompt_version) if self._renderer is not None else None

        attempts: list[AttemptUsage] = []
        last_failure = "no attempt made"
        for attempt in range(1, 2 + max_retries):
            self._gate.before_attempt(attempt)  # BudgetReachedError: not retried
            request = TransportRequest(
                prompt=prompt_version,
                input=input,
                model=self._model,
                attempt=attempt,
                timeout=self._timeout,
                rendered_prompt=rendered,
            )
            try:
                raw = self._transport.complete(request)
            except TransportTimeoutError:
                self._record(attempts, AttemptUsage(self._model, 0, 0, 0, "timeout"))
                last_failure = "timeout"
                continue
            except ProviderUnavailableError:
                self._record(attempts, AttemptUsage(self._model, 0, 0, 0, "unavailable"))
                raise
            except VendorProtocolError:
                # A configuration fault (HTTP 400 or 404): retrying cannot help.
                self._record(attempts, AttemptUsage(self._model, 0, 0, 0, "configuration"))
                raise

            if self._binding is not None and not self._binding.accepts(raw):
                self._record(attempts, self._usage(raw, "binding"))
                raise ProviderBindingError(
                    "response origin host or credential does not match the provider binding"
                )

            try:
                output = schema.validate(raw.text)
            except ValueError:
                self._record(attempts, self._usage(raw, "invalid"))
                last_failure = "invalid output"
                continue

            accepted = self._usage(raw, "ok")
            self._record(attempts, accepted)
            return StructuredOutput(
                output=output,
                prompt=prompt_version,
                model=accepted.model,
                attempts=tuple(attempts),
            )

        if last_failure == "timeout":
            raise ProviderUnavailableError(
                f"all {len(attempts)} attempts timed out for {prompt_version.prompt_id}"
            )
        raise MalformedOutputError(
            f"all {len(attempts)} attempts failed validation for {prompt_version.prompt_id}"
        )

    def _record(self, attempts: list[AttemptUsage], usage: AttemptUsage) -> None:
        attempts.append(usage)
        self._sink.record(usage)

    @staticmethod
    def _usage(raw: RawCompletion, outcome: str) -> AttemptUsage:
        return AttemptUsage(
            model=raw.model,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cost_cents=raw.cost_cents,
            outcome=outcome,
        )
