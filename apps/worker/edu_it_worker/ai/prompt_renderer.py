"""Render pinned prompts for the live provider (ai-content Req 1.7, 1.10).

Adapts :class:`PromptStore` to the ``PromptRenderer`` that ``build_llm_provider`` takes, so the
vendor adapter receives the rendered prompt text and the output schema name. Infrastructure does
not import the worker, so the worker supplies this at composition time.
"""

from __future__ import annotations

from edu_it_domain.ports.llm_provider import PromptRef
from edu_it_infrastructure.llm.transport import PromptRenderer, RenderedPrompt

from edu_it_worker.ai.prompt_store import PromptStore

__all__ = ["store_prompt_renderer"]


def store_prompt_renderer(store: PromptStore | None = None) -> PromptRenderer:
    """Return a renderer backed by ``store`` (default: the repository's ``prompts/``)."""
    prompts = store if store is not None else PromptStore()

    def render(ref: PromptRef) -> RenderedPrompt:
        prompt = prompts.load(ref.prompt_id, ref.prompt_version)
        return RenderedPrompt(
            text=prompts.render(ref.prompt_id, ref.prompt_version),
            output_schema=prompt.output_schema,
        )

    return render
