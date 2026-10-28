"""The in-memory ``LLMProvider`` fake (Req 5.2, 5.4, ADR 0007).

:class:`Fake_LLMProvider` implements the domain
:class:`~edu_it_domain.ports.llm_provider.LLMProvider` port entirely in process.
It makes **no network call** to a real provider (Req 5.4): completions are
produced from a deterministic, locally computed stub, optionally seeded with
canned responses keyed by prompt. This is the only ``LLMProvider`` implementation
in this phase; the production adapter arrives with the learning features (ADR
0007).

The fake is substitutable for a future production adapter without changing domain
code: it satisfies the same ``LLMProvider`` protocol and returns an
:class:`~edu_it_domain.ports.llm_provider.LLMCompletion` carrying text plus the
provenance (model, token counts, cost) the platform records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from edu_it_domain.ports.llm_provider import LLMCompletion, LLMProvider

__all__ = ["Fake_LLMProvider"]


class Fake_LLMProvider(LLMProvider):
    """An :class:`LLMProvider` that returns deterministic local completions.

    Satisfies the :class:`~edu_it_domain.ports.llm_provider.LLMProvider`
    protocol and performs no I/O, so dev/test exercise generation paths without
    reaching a real provider (Req 5.4).
    """

    __test__ = False

    def __init__(self, responses: Mapping[str, str] | None = None) -> None:
        """Create a provider, optionally seeded with canned responses.

        ``responses`` maps a prompt to the text to return for it. A prompt with
        no canned response gets a deterministic stub derived from the prompt, so
        every call still returns usable text without any network access.
        """
        self._responses: dict[str, str] = dict(responses) if responses is not None else {}
        self._calls: list[tuple[str, str]] = []

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        stop: Sequence[str] | None = None,
    ) -> LLMCompletion:
        """Return a deterministic completion for ``prompt`` (no network call).

        Uses the canned response for ``prompt`` when one was seeded, otherwise a
        stub derived from the prompt. ``stop`` sequences, when given, truncate
        the text at the first occurrence, mirroring a real provider's stop
        behavior. Token counts and cost are derived locally for provenance.
        """
        text = self._responses.get(prompt, self._stub(prompt))
        text = self._apply_stop(text, stop)
        self._calls.append((prompt, model))
        return LLMCompletion(
            text=text,
            model=model,
            prompt_tokens=self._count_tokens(prompt),
            completion_tokens=self._count_tokens(text),
            cost=0.0,
            metadata={"provider": "fake"},
        )

    @property
    def calls(self) -> tuple[tuple[str, str], ...]:
        """Every ``(prompt, model)`` completed so far, for test inspection."""
        return tuple(self._calls)

    @staticmethod
    def _stub(prompt: str) -> str:
        """Return a deterministic stub completion for ``prompt``."""
        return f"[fake-completion] {prompt}"

    @staticmethod
    def _apply_stop(text: str, stop: Sequence[str] | None) -> str:
        """Truncate ``text`` at the earliest of the ``stop`` sequences, if any."""
        if not stop:
            return text
        cut = len(text)
        for marker in stop:
            if marker:
                index = text.find(marker)
                if index != -1:
                    cut = min(cut, index)
        return text[:cut]

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Approximate a token count by whitespace-splitting ``text``."""
        return len(text.split())
