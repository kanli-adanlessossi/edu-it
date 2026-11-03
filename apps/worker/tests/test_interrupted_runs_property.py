"""Property 23: interrupted runs commit only complete units and resume exactly (task 8.7).

Runs the real Generator against real temporary git repositories, the in-memory ledger and the
fake providers. A scripted drafting provider decides, call by call, whether a unit succeeds,
comes back malformed, or stops the run (budget reached, provider unavailable, Ctrl-C). A pure
model of the expected outcome is compared with what the Generator did. Reruns on the
integrated result must generate exactly the plan minus the committed ``stable_id`` set.

**Validates: Requirements 12.2, 12.4, 12.6, 12.7, 12.8, 24.5, 24.9, 24.10, 24.11**
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from edu_it_application.ai import VerifyItem
from edu_it_domain.ports.llm_provider import (
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
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationOutcome,
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
LOCALES = ("en", "fr")
MAX_LESSONS = 4


def _loc(text: str) -> dict[str, str]:
    return {"en": text, "fr": f"{text} (fr)"}


def _outline(lessons: int) -> dict[str, object]:
    return {
        "modules": [
            {
                "title": _loc("Addresses"),
                "lessons": [
                    {
                        "title": _loc(f"Lesson {i}"),
                        "summary": _loc(f"Summary {i}"),
                        "concept_titles": [_loc(f"Concept {i}")],
                    }
                    for i in range(lessons)
                ],
            }
        ]
    }


BRIEF = {
    "title": _loc("Networking"),
    "description": _loc("How computers talk"),
    "learning_outcomes": [_loc("Explain an address")],
}
LESSON = {
    "title": _loc("A lesson"),
    "sections": [{"heading": _loc("Intro"), "body": _loc("Some prose."), "commands": []}],
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


class _Script:
    """Drafting provider: call n takes ``actions[n]`` (``ok`` once the script is exhausted)."""

    def __init__(self, good: Fake_LLMProvider, actions: tuple[str, ...]) -> None:
        self._good = good
        self._bad = Fake_LLMProvider({"lesson": "not json at all"})
        self._actions = actions
        self.calls = 0

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        action = self._actions[self.calls] if self.calls < len(self._actions) else "ok"
        self.calls += 1
        if action == "unavailable":
            raise ProviderUnavailableError("down")
        if action == "budget":
            raise BudgetReachedError("daily")
        if action == "interrupt":
            raise KeyboardInterrupt
        provider = self._bad if action == "malformed" else self._good
        return provider.complete(prompt_version, input, schema, max_retries)


ACTIONS = ("ok", "malformed", "budget", "unavailable", "interrupt")


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


class _Rig:
    def __init__(self, repo: Path, lessons: int) -> None:
        self.repo = repo
        self.lessons = lessons
        self.clock = Test_Clock()
        self.ledger = In_Memory_GenerationLedger(self.clock)
        self.git = SubprocessGitWorkspace(repo)
        self.ids = _Ids()
        self.script: _Script | None = None
        self.actions: tuple[str, ...] = ()

    def wire(self, scope: JobScope) -> RunProviders:
        gate, sink = LedgerAttemptGate(scope), LedgerUsageSink(scope)
        responses = {
            "brief": json.dumps(BRIEF),
            "outline": json.dumps(_outline(self.lessons)),
            "lesson": json.dumps(LESSON),
            "verify_answer": json.dumps({"selected": [0]}),
        }
        provider = Fake_LLMProvider(responses, gate=gate, sink=sink)
        self.script = _Script(provider, self.actions)
        verifier = VerifyItem(
            independent_llm=provider,
            sandbox=Fake_SandboxProvider(self.clock),
            lab_verifier=_NoLabs(),
        )
        return RunProviders(self.script, verifier)

    def run(self, stage: str, actions: tuple[str, ...] = ()) -> GenerationOutcome:
        self.actions = actions
        self.script = None
        generator = Generator(
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
        return generator.run(GenerationRequest(TRACK, stage))

    def integrate(self, outcome: GenerationOutcome) -> None:
        assert outcome.branch is not None
        _git(self.repo, "merge", "--ff-only", "-q", outcome.branch)

    def calls(self) -> int:
        return 0 if self.script is None else self.script.calls

    def lesson_ids(self) -> list[str]:
        text = (self.repo / BASE / "lessons.json").read_text(encoding="utf-8")
        return [item["provenance"]["stable_id"] for item in json.loads(text)]


def _build_template(root: Path, lessons: int) -> Path:
    repo = root / f"template-{lessons}"
    repo.mkdir()
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
    rig = _Rig(repo, lessons)
    for stage in ("brief", "outline"):
        outcome = rig.run(stage)
        assert outcome.exit_code is ExitCode.OK, outcome.message
        rig.integrate(outcome)
    return repo


@pytest.fixture(scope="module")
def templates(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    root = tmp_path_factory.mktemp("interrupted-runs")
    return {n: _build_template(root, n) for n in range(1, MAX_LESSONS + 1)}


def _expected(
    remaining: list[str], actions: tuple[str, ...]
) -> tuple[ExitCode, list[str], list[str], list[str]]:
    """Pure model: (exit code, committed, discarded, not generated) for one run."""
    committed: list[str] = []
    discarded: list[str] = []
    not_generated: list[str] = []
    code: ExitCode | None = None
    for index, unit in enumerate(remaining):
        action = actions[index] if index < len(actions) else "ok"
        if action == "ok":
            committed.append(unit)
        elif action == "malformed":
            discarded.append(unit)
        elif action == "unavailable":
            not_generated += remaining[index:]
            code = ExitCode.FAILED
            break
        else:  # budget or interrupt: the unit in flight is discarded
            discarded.append(unit)
            not_generated += remaining[index + 1 :]
            code = ExitCode.BUDGET if action == "budget" else ExitCode.INTERRUPTED
            break
    if code is None:
        code = ExitCode.FAILED if discarded else ExitCode.OK
    return code, committed, discarded, not_generated


def _branch_lesson_files(repo: Path, branch: str) -> set[str]:
    listed = _git(repo, "ls-tree", "-r", "--name-only", branch, f"{BASE}/mdx/")
    return set(listed.splitlines())


@settings(
    max_examples=10,  # each example drives real git repositories
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    lessons=st.integers(min_value=1, max_value=MAX_LESSONS),
    rounds=st.lists(
        st.lists(st.sampled_from(ACTIONS), min_size=0, max_size=MAX_LESSONS).map(tuple),
        min_size=1,
        max_size=3,
    ),
)
def test_interrupted_runs_commit_only_complete_units_and_resume_exactly(
    templates: dict[int, Path], lessons: int, rounds: list[tuple[str, ...]]
) -> None:
    """**Validates: Requirements 12.2, 12.4, 12.6, 12.7, 12.8, 24.5, 24.9, 24.10, 24.11**"""
    with tempfile.TemporaryDirectory(prefix="edu-it-p23-", ignore_cleanup_errors=True) as tmp:
        repo = Path(tmp) / "repo"
        shutil.copytree(templates[lessons], repo)
        rig = _Rig(repo, lessons)
        plan = rig.lesson_ids()
        assert len(plan) == lessons
        committed_total: list[str] = []

        for actions in rounds:
            remaining = [u for u in plan if u not in committed_total]
            outcome = rig.run("lessons", actions)

            if not remaining:
                assert outcome.exit_code is ExitCode.OK
                assert outcome.branch is None and rig.calls() == 0
                continue

            code, committed, discarded, not_generated = _expected(remaining, actions)
            # Exit code names the failure kind (12.4, 12.7, 24.9, 24.10).
            assert outcome.exit_code is code, outcome.message
            if code is ExitCode.BUDGET:
                assert "daily" in outcome.message
            if code in (ExitCode.FAILED, ExitCode.BUDGET, ExitCode.INTERRUPTED):
                assert outcome.exit_code is not ExitCode.OK

            # Committed set, discarded and not generated units match the model exactly (12.2).
            assert list(outcome.committed) == committed
            assert [u.stable_id for u in outcome.discarded] == discarded
            assert [u.stable_id for u in outcome.not_generated] == not_generated
            assert set(committed) | set(discarded) | set(not_generated) == set(remaining)

            # No call after the stop: the provider saw only attempted units.
            attempted = len(remaining) - len(not_generated)
            if code is ExitCode.FAILED and not_generated:
                attempted += 1  # the unavailable call itself
            assert rig.calls() == attempted

            # Only units complete in both locales are on the branch; partial ones are absent.
            assert outcome.branch is not None
            on_branch = _branch_lesson_files(repo, outcome.branch)
            for unit in remaining:
                files = {f"{BASE}/mdx/{unit}.{loc}.mdx" for loc in LOCALES}
                if unit in committed:
                    assert files <= on_branch
                else:
                    assert not (files & on_branch)

            # The report lists discarded units and items not generated (12.8).
            report = _git(
                repo,
                "show",
                f"{outcome.branch}:docs/generation/{TRACK}/lessons-{outcome.run_id}.md",
            )
            for unit in (*discarded, *not_generated):
                assert unit in report

            rig.integrate(outcome)
            committed_total += committed
            assert len(set(committed_total)) == len(committed_total)  # committed once

        # A clean rerun generates exactly the plan minus the committed set (12.6, 24.11).
        remaining = [u for u in plan if u not in committed_total]
        final = rig.run("lessons")
        if remaining:
            assert final.exit_code is ExitCode.OK, final.message
            assert list(final.committed) == remaining
            assert final.discarded == () and final.not_generated == ()
            assert rig.calls() == len(remaining)
            rig.integrate(final)
            committed_total += list(final.committed)
        else:
            assert final.branch is None and rig.calls() == 0
        assert sorted(committed_total) == sorted(plan)
        assert len(committed_total) == len(set(committed_total))

        # Once everything is committed, a further run does nothing.
        again = rig.run("lessons")
        assert again.exit_code is ExitCode.OK and again.branch is None and rig.calls() == 0
