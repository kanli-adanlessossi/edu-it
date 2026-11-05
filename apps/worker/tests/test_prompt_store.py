"""Prompt store: parsing, hashing, includes and the shipped v1 prompts (ai-content Req 4)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from edu_it_worker.ai.prompt_store import (
    GLOSSARY_PROMPT_ID,
    PromptFormatError,
    PromptNotFoundError,
    PromptStore,
    glossary_pairs,
)

_DRAFTING = ("brief", "outline", "lesson", "quiz", "lab", "project", "variant")


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8", newline="\n")


def test_load_parses_front_matter_body_and_hash(tmp_path: Path) -> None:
    text = "---\noutput_schema: S\ninput_kind: brief\nincludes: g@2, h@1\n---\nHello\n"
    _write(tmp_path, "p__v3.md", text)

    prompt = PromptStore(tmp_path).load("p", "3")

    assert (prompt.output_schema, prompt.input_kind) == ("S", "brief")
    assert prompt.includes == (("g", "2"), ("h", "1"))
    assert prompt.body == "Hello\n"
    assert prompt.sha256 == hashlib.sha256(text.encode()).hexdigest()


def test_missing_file_and_bad_front_matter_are_rejected(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    with pytest.raises(PromptNotFoundError):
        store.load("nope", "1")
    _write(tmp_path, "bad__v1.md", "no front matter\n")
    with pytest.raises(PromptFormatError):
        store.load("bad", "1")
    _write(tmp_path, "key__v1.md", "---\noutput_schema: S\n---\nx\n")
    with pytest.raises(PromptFormatError):
        store.load("key", "1")


def test_render_expands_includes_first_and_rejects_cycles(tmp_path: Path) -> None:
    head = "---\noutput_schema: none\ninput_kind: none\nincludes: {inc}\n---\n{body}\n"
    _write(tmp_path, "g__v1.md", head.format(inc="", body="GLOSS"))
    _write(tmp_path, "p__v1.md", head.format(inc="g@1", body="MAIN"))
    _write(tmp_path, "a__v1.md", head.format(inc="b@1", body="A"))
    _write(tmp_path, "b__v1.md", head.format(inc="a@1", body="B"))
    store = PromptStore(tmp_path)

    assert store.render("p", "1") == "GLOSS\n\nMAIN\n"
    with pytest.raises(PromptFormatError):
        store.render("a", "1")


@pytest.mark.parametrize("prompt_id", _DRAFTING)
def test_shipped_drafting_prompts_include_the_glossary(prompt_id: str) -> None:
    store = PromptStore()
    prompt = store.load(prompt_id, "1")
    assert (GLOSSARY_PROMPT_ID, "1") in prompt.includes
    assert prompt.input_kind == prompt_id
    assert store.render(prompt_id, "1").startswith(store.load("glossary", "1").body)


def test_shipped_verify_answer_is_blind_and_glossary_has_pairs() -> None:
    store = PromptStore()
    verify = store.load("verify_answer", "1")
    assert verify.input_kind == "verify_answer"
    assert verify.includes == ()
    assert ("IP address", "adresse IP") in glossary_pairs(store.load("glossary", "1"))


def test_default_prompts_dir_honours_the_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edu_it_worker.ai.prompt_store import PROMPTS_ROOT_ENV, default_prompts_dir

    monkeypatch.delenv(PROMPTS_ROOT_ENV, raising=False)
    assert default_prompts_dir().name == "prompts"

    monkeypatch.setenv(PROMPTS_ROOT_ENV, str(tmp_path))
    assert default_prompts_dir() == tmp_path
