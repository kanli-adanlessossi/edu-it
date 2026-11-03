"""Static and gate tests for the Generator (task 8.8).

Complements ``test_generator.py`` and ``test_generator_git_stages.py``: every module of the
generator package is scanned, every Stage is gated on its predecessor, and no CLI option or
request field can bypass the gates. No model is called and no network is used.

**Validates: Requirements 5.1, 5.9, 5.13, 5.16, 10.5**
"""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
from pathlib import Path

import edu_it_worker.ai as ai_package
import pytest
from edu_it_domain.ai.generation import STAGES
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_worker.ai.cli import build_parser
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_store import PromptStore

TRACK = "networking-basics"
PACKAGE_DIR = Path(ai_package.__file__).resolve().parent
MODULES = sorted(PACKAGE_DIR.glob("*.py"))


class _Ids:
    def new_id(self) -> str:
        return "run1"


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
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "trunk")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def _generator(repo: Path, wired: list[object]) -> Generator:
    clock = Test_Clock()

    def wire(scope: object) -> RunProviders:
        wired.append(scope)
        raise AssertionError("a refused request must not reach the providers")

    return Generator(
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


def test_package_has_modules_to_scan() -> None:
    names = {m.name for m in MODULES}
    assert {"generator.py", "cli.py", "units.py", "stages.py"} <= names


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_module_names_join_publish_or_protected_branches(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    assert not re.search(r"""["'](main|master|trunk|develop|release)["']""", source)
    assert not re.search(r"\b(master|develop)\b", source, re.I)
    # Prose in these two modules legitimately says "merge" (a PR warning; merging JSON item
    # lists by stable_id). Neither touches git, so they are held to the identifier check below.
    if module.name not in {"content_writer.py", "report.py"}:
        assert not re.search(r"\b(merge[sd]?|push(ed|es)?|rebase|force)\b", source, re.I)
    tree = ast.parse(source)
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert identifiers.isdisjoint({"merge", "push", "rebase", "checkout", "pull", "fetch"})


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_module_runs_a_process_directly(module: Path) -> None:
    """Git is reached only through the GitWorkspace port, never through a subprocess."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for alias in n.names
    } | {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported.isdisjoint({"subprocess", "dulwich", "git"})


@pytest.mark.parametrize("stage", STAGES[1:])
def test_every_stage_after_the_first_is_refused_without_its_predecessor(
    repo: Path, stage: str
) -> None:
    wired: list[object] = []
    before = _git(repo, "branch", "--list")
    outcome = _generator(repo, wired).run(GenerationRequest(TRACK, stage))
    assert outcome.exit_code is ExitCode.STAGE_GATE
    assert outcome.branch is None
    assert wired == []
    assert _git(repo, "branch", "--list") == before


@pytest.mark.parametrize("stage", STAGES[1:])
def test_a_named_base_ref_without_the_markers_does_not_open_the_gate(
    repo: Path, stage: str
) -> None:
    wired: list[object] = []
    outcome = _generator(repo, wired).run(GenerationRequest(TRACK, stage, base_ref="trunk"))
    assert outcome.exit_code is ExitCode.STAGE_GATE
    assert wired == []


def test_request_fields_offer_no_way_to_skip_a_gate() -> None:
    fields = {f.name for f in dataclasses.fields(GenerationRequest)}
    assert fields == {
        "track",
        "stage",
        "live",
        "cost_cap_cents",
        "limit_units",
        "base_ref",
    }


@pytest.mark.parametrize(
    "flag",
    ["--force", "--skip-gate", "--skip-stage-gate", "--no-budget", "--ignore-budget", "--merge"],
)
def test_bypass_flags_are_rejected_by_the_parser(flag: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--track", TRACK, "--stage", "outline", flag])


def test_stage_choices_are_exactly_the_domain_stages() -> None:
    stage = next(a for a in build_parser()._actions if a.dest == "stage")
    assert tuple(stage.choices or ()) == tuple(STAGES)
