"""Property test for generated items (task 8.5).

Feature: ai-content, Property 10: Generated items are unreviewed, bilingual, pinned, not servable.

Runs the real Generator through every Stage against a temporary git repository with the
fake providers, then reads back what the content writer committed. No model, no network.

**Validates: Requirements 4.5, 5.8, 5.10, 22.1, 24.8**
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from edu_it_application.ai import VerifyItem
from edu_it_content.validation import load_content, validate_content
from edu_it_domain.ports.llm_provider import (
    LLMInput,
    OutputSchema,
    PromptRef,
    StructuredOutput,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_store import PromptStore
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

TRACK = "networking-basics"
BASE = f"content/{TRACK}"
STAGES = ("brief", "outline", "lessons", "quizzes", "labs", "project")

WORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=10)


def _loc(text: str) -> dict[str, str]:
    return {"en": text, "fr": f"{text} (fr)"}


def _canned(word: str, lessons: int) -> dict[str, Any]:
    return {
        "brief": {
            "title": _loc(f"Track {word}"),
            "description": _loc(f"About {word}"),
            "learning_outcomes": [_loc(f"Explain {word}")],
        },
        "outline": {
            "modules": [
                {
                    "title": _loc(f"Module {word}"),
                    "lessons": [
                        {
                            "title": _loc(f"Lesson {word} {i}"),
                            "summary": _loc(f"Summary {word} {i}"),
                            "concept_titles": [_loc(f"Concept {i}")],
                        }
                        for i in range(lessons)
                    ],
                }
            ]
        },
        "lesson": {
            "title": _loc(f"Reading {word}"),
            "sections": [{"heading": _loc("Intro"), "body": _loc("Some prose."), "commands": []}],
        },
        "quiz": {
            "title": _loc(f"Quiz {word}"),
            "questions": [
                {
                    "prompt_text": _loc(f"What is {word}?"),
                    "options": [
                        {"text": _loc("One"), "is_correct": True},
                        {"text": _loc("Two"), "is_correct": False},
                    ],
                    "explanation": _loc("Because."),
                    "concept_title": _loc("Concept 0"),
                }
            ],
        },
        "lab": {
            "title": _loc(f"Lab {word}"),
            "kind": "terminal",
            "instructions": _loc("Make the folder."),
            "setup_script": "mkdir -p /workspace",
            "tasks": [
                {
                    "title": _loc("Make it"),
                    "description": _loc("Create the folder."),
                    "hints": [],
                    "checks": [
                        {
                            "script": "test -d /workspace/x",
                            "timeout_seconds": 2,
                            "pass_message": _loc("Done."),
                            "fail_message": _loc("Not yet."),
                        }
                    ],
                }
            ],
            "reference_solution": "mkdir -p /workspace/x",
        },
        "project": {
            "title": _loc(f"Project {word}"),
            "description": _loc("Build a thing."),
            "criteria": [{"title": _loc("Correct"), "max_points": 10}],
        },
    }


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"run{self._n}"


class _NoLabs:
    def execute(self, lab: object) -> tuple[()]:
        return ()


class _Recording:
    """Delegates to the fake and remembers every PromptRef the Generator pinned."""

    def __init__(self, inner: Fake_LLMProvider, refs: list[PromptRef]) -> None:
        self._inner = inner
        self._refs = refs

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self._refs.append(prompt_version)
        return self._inner.complete(prompt_version, input, schema, max_retries)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "trunk")
    for rel, text in (
        ("lab-images/linux-base/Dockerfile", "FROM scratch\n"),
        (".github/CODEOWNERS", ""),
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")


def _generate_all(repo: Path, canned: dict[str, Any], refs: list[PromptRef]) -> None:
    clock = Test_Clock()
    responses = {k: json.dumps(v) for k, v in canned.items()}
    responses["verify_answer"] = json.dumps({"selected": [0]})

    def wire(scope: JobScope) -> RunProviders:
        provider = Fake_LLMProvider(responses)
        verifier = VerifyItem(
            independent_llm=provider,
            sandbox=Fake_SandboxProvider(clock),
            lab_verifier=_NoLabs(),
        )
        return RunProviders(_Recording(provider, refs), verifier)

    generator = Generator(
        GeneratorDeps(
            git=SubprocessGitWorkspace(repo),
            ledger=In_Memory_GenerationLedger(clock),
            clock=clock,
            ids=_Ids(),
            prompts=PromptStore(),
            wire=wire,
            base_branch="trunk",
        )
    )
    for stage in STAGES:
        outcome = generator.run(GenerationRequest(TRACK, stage))
        assert outcome.exit_code is ExitCode.OK, outcome.message
        assert outcome.branch is not None
        _git(repo, "merge", "--ff-only", "-q", outcome.branch)


def _items(repo: Path) -> Iterator[dict[str, Any]]:
    """Every provenanced item written under the track directory."""
    for path in sorted((repo / BASE).glob("*.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        for entry in loaded if isinstance(loaded, list) else [loaded]:
            if isinstance(entry, dict) and "provenance" in entry:
                yield entry


def _localized_values(value: object) -> Iterator[dict[str, str]]:
    """Every FR/EN pair found anywhere inside ``value``."""
    if isinstance(value, dict):
        if set(value) == {"en", "fr"}:
            yield value
        else:
            for child in value.values():
                yield from _localized_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _localized_values(child)


def _is_approved(provenance: dict[str, Any]) -> bool:
    """The authored-item approval rule (design F3, 14.1): human, or AI with both reviewer fields."""
    if provenance["origin"] == "human":
        return True
    return bool(provenance.get("reviewed_by")) and bool(provenance.get("reviewed_at"))


@settings(
    max_examples=6,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(word=WORDS, lessons=st.integers(min_value=1, max_value=3))
def test_generated_items_are_unreviewed_bilingual_pinned_and_not_servable(
    word: str, lessons: int
) -> None:
    """Feature: ai-content, Property 10: Generated items are unreviewed and not servable.

    **Validates: Requirements 4.5, 5.8, 5.10, 22.1, 24.8**
    """
    refs: list[PromptRef] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _init_repo(repo)
        _generate_all(repo, _canned(word, lessons), refs)

        items = list(_items(repo))
        assert len(items) >= 1 + lessons  # at least the track and every lesson

        pinned = {(r.prompt_id, r.prompt_version) for r in refs}
        assert pinned

        for item in items:
            prov = item["provenance"]
            # origin ai, no reviewer trail
            assert prov["origin"] == "ai"
            assert not prov.get("reviewed_by")
            assert not prov.get("reviewed_at")
            # pinned to the PromptRef that produced it
            assert (prov["prompt_id"], prov["prompt_version"]) in pinned
            # not servable: never approved
            assert not _is_approved(prov)
            # both locales for every learner-facing field
            for pair in _localized_values(item):
                assert pair["en"].strip() and pair["fr"].strip()

        # every lesson body exists in both locales and is not empty
        for lesson in (i for i in items if i["provenance"]["stable_id"].startswith("lesson.")):
            sid = lesson["provenance"]["stable_id"]
            for locale in ("fr", "en"):
                body = repo / BASE / "mdx" / f"{sid}.{locale}.mdx"
                assert body.is_file()
                assert body.read_text(encoding="utf-8").strip()

        # the validator agrees: structure is fine, and the only failure is the review gate
        content = load_content(repo / "content")
        assert validate_content(content, repo_root=repo, include_review_rule=False) == []
        strict = validate_content(content, repo_root=repo)
        assert strict
        assert {f.rule for f in strict} == {"ai-reviewer-present"}
