"""Governance data: glossary coverage, CODEOWNERS per track, glossary inclusion.

Validates: Requirements 4.4, 4.6, 5.7
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edu_it_worker.ai.prompt_store import PromptStore, default_prompts_dir, glossary_pairs

_REPO = default_prompts_dir().parent
_DRAFTING = ("brief", "outline", "lesson", "quiz", "lab", "project", "variant")


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_glossary_contains_every_linux_concept_title_pair() -> None:
    """Validates: Requirements 4.6"""
    pairs = set(glossary_pairs(PromptStore().load("glossary", "1")))
    concepts = _load_json(_REPO / "content" / "linux-fundamentals" / "concepts.json")
    assert isinstance(concepts, list)
    assert concepts
    for concept in concepts:
        title = concept["title"]
        assert (title["en"], title["fr"]) in pairs


def test_glossary_contains_every_networking_seed_term() -> None:
    """Validates: Requirements 4.6, 5.7"""
    pairs = set(glossary_pairs(PromptStore().load("glossary", "1")))
    seed = _load_json(_REPO / "evals" / "glossary" / "networking-terms.json")
    assert isinstance(seed, dict)
    terms = seed["terms"]
    assert terms
    for term in terms:
        assert (term["term_en"], term["term_fr"]) in pairs


def test_codeowners_has_a_line_per_content_track() -> None:
    """Validates: Requirements 5.7"""
    lines = [
        line.split()
        for line in (_REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    owners = {fields[0].rstrip("/"): fields[1:] for fields in lines}
    tracks = sorted(p.name for p in (_REPO / "content").iterdir() if p.is_dir())
    assert tracks
    for track in tracks:
        assert owners.get(f"/content/{track}"), f"no CODEOWNERS owner for {track}"


@pytest.mark.parametrize("prompt_id", _DRAFTING)
def test_each_drafting_prompt_includes_glossary_v1(prompt_id: str) -> None:
    """Validates: Requirements 4.4"""
    assert ("glossary", "1") in PromptStore().load(prompt_id, "1").includes
