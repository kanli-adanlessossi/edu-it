"""Tests for the Generator and ``edu-it-generate`` (task 8.3).

Run against real temporary git repositories, the in-memory ledger and the fake providers.
No model is called and no network is used.

**Validates: Requirements 5.2, 5.3, 5.5, 5.9, 5.11, 5.12, 5.13, 5.14, 5.15, 5.16, 10.1, 10.2,
10.3, 10.4, 10.5, 12.1, 12.2, 12.4, 12.6, 12.7, 12.8**
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import edu_it_worker.ai.cli as cli_module
import edu_it_worker.ai.generator as generator_module
import edu_it_worker.ai.units as units_module
import pytest
from edu_it_application.ai import VerifyItem
from edu_it_content.validation import load_content, validate_content
from edu_it_domain.ports.generation_ledger import JobKind, JobStatus, NewJob
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    OutputSchema,
    PromptRef,
    ProviderUnavailableError,
    StructuredOutput,
)
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_infrastructure.llm.ledger_binding import JobScope, LedgerAttemptGate, LedgerUsageSink
from edu_it_worker.ai.cli import build_parser, cost_cap_cents
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationOutcome,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_store import PromptStore

TRACK = "networking-basics"
BASE = f"content/{TRACK}"


def _loc(text: str) -> dict[str, str]:
    return {"en": text, "fr": f"{text} (fr)"}


BRIEF = {
    "title": _loc("Networking"),
    "description": _loc("How computers talk"),
    "learning_outcomes": [_loc("Explain an address")],
}
OUTLINE = {
    "modules": [
        {
            "title": _loc("Addresses"),
            "lessons": [
                {
                    "title": _loc("What is an IP address"),
                    "summary": _loc("Addresses name hosts"),
                    "concept_titles": [_loc("IP address")],
                },
                {
                    "title": _loc("What is a subnet"),
                    "summary": _loc("Subnets group hosts"),
                    "concept_titles": [_loc("Subnet")],
                },
            ],
        }
    ]
}
LESSON = {
    "title": _loc("A lesson"),
    "sections": [{"heading": _loc("Intro"), "body": _loc("Some prose."), "commands": []}],
}
QUIZ = {
    "title": _loc("A quiz"),
    "questions": [
        {
            "prompt_text": _loc("What is 1?"),
            "options": [
                {"text": _loc("One"), "is_correct": True},
                {"text": _loc("Two"), "is_correct": False},
            ],
            "explanation": _loc("Because."),
            "concept_title": _loc("IP address"),
        }
    ],
}
LAB = {
    "title": _loc("A lab"),
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
}
PROJECT = {
    "title": _loc("A project"),
    "description": _loc("Build a thing."),
    "criteria": [{"title": _loc("Correct"), "max_points": 10}],
}
CANNED = {
    "brief": BRIEF,
    "outline": OUTLINE,
    "lesson": LESSON,
    "quiz": QUIZ,
    "lab": LAB,
    "project": PROJECT,
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


class _Spy:
    """Wraps a provider and counts the drafting calls."""

    def __init__(self, inner: Fake_LLMProvider) -> None:
        self.inner = inner
        self.calls = 0

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.calls += 1
        return self.inner.complete(prompt_version, input, schema, max_retries)


class _Scripted:
    """Raises ``error`` on the n-th call and delegates before that."""

    def __init__(self, inner: Fake_LLMProvider, fail_on: int, error: Exception) -> None:
        self.inner, self.fail_on, self.error, self.calls = inner, fail_on, error, 0

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.calls += 1
        if self.calls == self.fail_on:
            raise self.error
        return self.inner.complete(prompt_version, input, schema, max_retries)


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


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "trunk")
    _write(tmp_path, "lab-images/linux-base/Dockerfile", "FROM scratch\n")
    _write(tmp_path, ".github/CODEOWNERS", "")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


class _Rig:
    def __init__(self, repo: Path, llm: object | None = None) -> None:
        self.repo = repo
        self.clock = Test_Clock()
        self.ledger = In_Memory_GenerationLedger(self.clock)
        self.git = SubprocessGitWorkspace(repo)
        self.spy = _Spy(Fake_LLMProvider())
        self.llm_override = llm
        self.drafting: list[object] = []
        self.ids = _Ids()

    def wire(self, scope: JobScope) -> RunProviders:
        gate, sink = LedgerAttemptGate(scope), LedgerUsageSink(scope)
        responses = {k: json.dumps(v) for k, v in CANNED.items()}
        responses["verify_answer"] = json.dumps({"selected": [0]})
        provider = Fake_LLMProvider(responses, gate=gate, sink=sink)
        llm: object = self.llm_override if self.llm_override is not None else _Spy(provider)
        self.drafting.append(llm)
        verifier = VerifyItem(
            independent_llm=provider,
            sandbox=Fake_SandboxProvider(self.clock),
            lab_verifier=_NoLabs(),
        )
        return RunProviders(llm, verifier)  # type: ignore[arg-type]

    def generator(self) -> Generator:
        return Generator(
            GeneratorDeps(
                git=self.git,
                ledger=self.ledger,
                clock=self.clock,
                ids=self.ids,
                prompts=PromptStore(),
                wire=self.wire,
                base_branch="trunk",
            )
        )

    def run(self, stage: str, **kwargs: object) -> GenerationOutcome:
        return self.generator().run(GenerationRequest(TRACK, stage, **kwargs))  # type: ignore[arg-type]

    def integrate(self, branch: str) -> None:
        _git(self.repo, "merge", "--ff-only", "-q", branch)

    def branches(self) -> list[str]:
        return _git(self.repo, "branch", "--list", "generate/*").split()

    def jobs(self) -> int:
        return len(self.ledger._jobs)


def _all_stages(rig: _Rig, upto: str) -> None:
    for stage in ("brief", "outline", "lessons", "quizzes", "labs", "project"):
        outcome = rig.run(stage)
        assert outcome.exit_code is ExitCode.OK, outcome.message
        assert outcome.branch is not None
        rig.integrate(outcome.branch)
        if stage == upto:
            return


def test_stage_gate_refuses_without_job_branch_or_model_call(repo: Path) -> None:
    rig = _Rig(repo)
    outcome = rig.run("outline")
    assert outcome.exit_code is ExitCode.STAGE_GATE
    assert "'brief'" in outcome.message
    assert rig.branches() == []
    assert rig.jobs() == 0
    assert rig.drafting == []


def test_named_base_ref_widens_where_markers_are_read_and_integrates_nothing(repo: Path) -> None:
    rig = _Rig(repo)
    brief = rig.run("brief")
    assert brief.exit_code is ExitCode.OK
    assert brief.branch is not None
    before = _git(repo, "rev-parse", "trunk")

    assert rig.run("outline").exit_code is ExitCode.STAGE_GATE
    unknown = rig.run("outline", base_ref="generate/nowhere/brief/x")
    assert unknown.exit_code is ExitCode.STAGE_GATE
    assert unknown.branch is None

    outline = rig.run("outline", base_ref=brief.branch)
    assert outline.exit_code is ExitCode.OK, outline.message
    assert _git(repo, "rev-parse", "trunk") == before  # nothing was integrated
    assert f"{BASE}/brief.json" not in _git(repo, "ls-tree", "-r", "--name-only", "trunk")
    assert f"{BASE}/lessons.json" in _git(
        repo, "ls-tree", "-r", "--name-only", outline.branch or ""
    )


def test_budget_gate_refuses_before_any_job_branch_or_model_call(repo: Path) -> None:
    rig = _Rig(repo)
    old = rig.ledger.open_job(NewJob("old", JobKind.STAGE, track_stable_id=f"track.{TRACK}"))
    rig.ledger.record_attempt(old, AttemptUsage("m", 1, 1, 2500, "ok"))
    jobs_before = rig.jobs()

    outcome = rig.run("brief")

    assert outcome.exit_code is ExitCode.BUDGET
    assert "daily" in outcome.message
    assert rig.branches() == []
    assert rig.jobs() == jobs_before
    assert rig.drafting == []


def test_cost_cap_is_part_of_the_budget_gate_and_cannot_be_bypassed(repo: Path) -> None:
    rig = _Rig(repo)
    flat = rig.run("brief", cost_cap_cents=0)
    assert flat.exit_code is ExitCode.USAGE
    assert rig.branches() == [] and rig.drafting == []


def test_stages_run_in_order_and_items_are_unreviewed_ai_with_both_locales(repo: Path) -> None:
    rig = _Rig(repo)
    _all_stages(rig, "project")

    content = load_content(repo / "content")
    strict = validate_content(content, repo_root=repo)
    assert strict, "strict validation must reject the unreviewed AI items"
    assert {f.rule for f in strict} == {"ai-reviewer-present"}
    assert validate_content(content, repo_root=repo, include_review_rule=False) == []

    lessons = json.loads((repo / BASE / "lessons.json").read_text(encoding="utf-8"))
    assert len(lessons) == 2
    for item in lessons:
        prov = item["provenance"]
        assert prov["origin"] == "ai"
        assert "reviewed_by" not in prov and "reviewed_at" not in prov
        assert prov["content_version"].startswith("sha256:")
        assert prov["prompt_id"] == "lesson"
        for locale in ("fr", "en"):
            assert (
                repo / BASE / "mdx" / f"{item['provenance']['stable_id']}.{locale}.mdx"
            ).is_file()
    assert (repo / BASE / "lab-bundles").is_dir()
    assert (repo / "docs" / "generation" / TRACK).is_dir()
    assert (repo / ".github" / "PULL_REQUEST_TEMPLATE" / "ai-generation-project.md").is_file()
    assert f"/content/{TRACK}/" in (repo / ".github" / "CODEOWNERS").read_text(encoding="utf-8")


def test_resume_skips_committed_units_and_limit_units_lists_the_rest(repo: Path) -> None:
    rig = _Rig(repo)
    _all_stages(rig, "outline")

    first = rig.run("lessons", limit_units=1)
    assert first.exit_code is ExitCode.OK
    assert len(first.committed) == 1
    assert [u.stable_id for u in first.not_generated] and "--limit-units" in first.not_generated[
        0
    ].reason
    assert first.branch is not None
    rig.integrate(first.branch)

    second = rig.run("lessons")
    assert second.exit_code is ExitCode.OK, second.message
    assert len(second.committed) == 1
    assert set(second.committed).isdisjoint(first.committed)
    assert second.branch is not None
    rig.integrate(second.branch)

    third = rig.run("lessons")
    assert third.exit_code is ExitCode.OK
    assert third.branch is None and "nothing to generate" in third.message


def test_malformed_output_discards_the_unit_and_never_corrects_it(repo: Path) -> None:
    rig = _Rig(repo)
    _all_stages(rig, "outline")
    bad = Fake_LLMProvider({"lesson": "not json at all"})
    rig.llm_override = bad

    outcome = rig.run("lessons")

    assert outcome.exit_code is ExitCode.FAILED
    assert outcome.committed == ()
    assert len(outcome.discarded) == 2
    assert outcome.branch is not None
    path = f"docs/generation/{TRACK}/lessons-{outcome.run_id}.md"
    assert "malformed output" in _git(repo, "show", f"{outcome.branch}:{path}")


def test_budget_reached_mid_run_commits_complete_units_and_lists_the_discarded(repo: Path) -> None:
    rig = _Rig(repo)
    _all_stages(rig, "outline")
    real = Fake_LLMProvider({"lesson": json.dumps(LESSON)})
    rig.llm_override = _Scripted(real, fail_on=2, error=BudgetReachedError("daily"))

    outcome = rig.run("lessons")

    assert outcome.exit_code is ExitCode.BUDGET
    assert "daily" in outcome.message
    assert len(outcome.committed) == 1
    assert [u.reason for u in outcome.discarded] == ["stopped by the daily budget"]
    assert outcome.not_generated == ()
    assert rig.llm_override.calls == 2  # no call after the stop


def test_provider_unavailable_stops_the_stage_and_lists_items_not_generated(repo: Path) -> None:
    rig = _Rig(repo)
    _all_stages(rig, "outline")
    real = Fake_LLMProvider({"lesson": json.dumps(LESSON)})
    rig.llm_override = _Scripted(real, fail_on=1, error=ProviderUnavailableError("down"))

    outcome = rig.run("lessons")

    assert outcome.exit_code is ExitCode.FAILED
    assert outcome.committed == ()
    assert len(outcome.not_generated) == 2
    assert outcome.branch is not None
    assert rig.llm_override.calls == 1


def test_job_is_closed_with_the_status_of_the_run(repo: Path) -> None:
    rig = _Rig(repo)
    outcome = rig.run("brief")
    assert outcome.run_id is not None
    assert rig.ledger._jobs[outcome.run_id].status is JobStatus.COMPLETED


def test_invalid_requests_make_no_job_branch_or_call(repo: Path) -> None:
    rig = _Rig(repo)
    for request in (
        GenerationRequest(TRACK, "nonsense"),
        GenerationRequest("../escape", "brief"),
        GenerationRequest(TRACK, "brief", limit_units=0),
    ):
        assert rig.generator().run(request).exit_code is ExitCode.USAGE
    assert rig.branches() == [] and rig.jobs() == 0 and rig.drafting == []


def test_cost_cap_parsing_and_cli_has_no_gate_override() -> None:
    assert cost_cap_cents("20") == 2000
    assert cost_cap_cents("0.005") == 1
    with pytest.raises(Exception):  # noqa: B017
        cost_cap_cents("0")
    with pytest.raises(Exception):  # noqa: B017
        cost_cap_cents("abc")
    options = {a.dest for a in build_parser()._actions}
    assert options == {
        "help",
        "track",
        "stage",
        "live",
        "record",
        "cost_cap_usd",
        "limit_units",
        "base_ref",
    }
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--track", "t", "--stage", "brief", "--live", "--record"])


@pytest.mark.parametrize("module", [generator_module, cli_module, units_module])
def test_generator_package_names_no_join_publish_or_protected_branch(module: object) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert not re.search(r"\b(merge[sd]?|push(ed|es)?|force|master|develop)\b", source, re.I)
    assert not re.search(r"""["'](main|master|trunk|develop|release)["']""", source)
