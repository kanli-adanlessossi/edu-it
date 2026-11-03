"""The PromptStore-backed renderer handed to the live provider.

**Validates: Requirements 1.7, 1.10**
"""

from __future__ import annotations

from pathlib import Path

from edu_it_domain.ports.llm_provider import PromptRef
from edu_it_worker.ai.prompt_renderer import store_prompt_renderer
from edu_it_worker.ai.prompt_store import PromptStore


def test_renders_included_prompts_and_reports_the_output_schema(tmp_path: Path) -> None:
    (tmp_path / "g__v1.md").write_text(
        "---\noutput_schema: none\ninput_kind: none\nincludes:\n---\nGLOSS\n", encoding="utf-8"
    )
    (tmp_path / "p__v2.md").write_text(
        "---\noutput_schema: LessonSchema\ninput_kind: brief\nincludes: g@1\n---\nMAIN\n",
        encoding="utf-8",
    )

    rendered = store_prompt_renderer(PromptStore(tmp_path))(PromptRef("p", "2"))

    assert rendered.text == "GLOSS\n\nMAIN\n"
    assert rendered.output_schema == "LessonSchema"


def test_default_store_renders_a_shipped_prompt() -> None:
    rendered = store_prompt_renderer()(PromptRef("lesson", "1"))

    assert rendered.text.strip()
    assert rendered.output_schema != "none"
