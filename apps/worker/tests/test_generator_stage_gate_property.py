"""Property test for the Generator's stage gate (task 8.4).

Drives the real :class:`Generator` over an in-memory :class:`GitWorkspace` that records every
call, the in-memory ledger and the fake providers. No model, network or real repository is used.

Feature: ai-content, Property 9: The stage gate depends only on the preceding Stage

**Validates: Requirements 5.3, 5.4, 5.11, 5.12, 5.14, 5.15, 5.16, 24.12**
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping

from edu_it_application.ai import VerifyItem
from edu_it_domain.ai.generation import STAGES
from edu_it_domain.ports.git_workspace import generation_branch_name
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.generation_ledger import In_Memory_GenerationLedger
from edu_it_infrastructure.fakes.llm_provider import Fake_LLMProvider
from edu_it_infrastructure.fakes.sandbox_provider import Fake_SandboxProvider
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_worker.ai.generator import (
    ExitCode,
    GenerationRequest,
    Generator,
    GeneratorDeps,
    RunProviders,
)
from edu_it_worker.ai.prompt_store import PromptStore
from hypothesis import given, settings
from hypothesis import strategies as st

BASE_BRANCH = "trunk"
NAMED_REF = "generate/networking-basics/outline/earlier"
LESSON_IDS = ("lesson.a", "lesson.b")

# Marker files per Stage, written independently of the production code (Req 5.1).
_JSON_MARKERS = {
    "brief": ("brief.json",),
    "outline": (
        "tracks.json",
        "modules.json",
        "lessons.json",
        "concepts.json",
        "prerequisites.json",
    ),
    "quizzes": ("quizzes.json", "questions.json", "question_options.json"),
    "labs": ("labs.json", "lab_tasks.json", "lab_checks.json"),
    "project": ("projects.json", "rubrics.json", "rubric_criteria.json"),
}

_slugs = st.from_regex(r"[a-z][a-z0-9]{0,9}(-[a-z0-9]{1,9}){0,3}", fullmatch=True)
_stages = st.sampled_from(STAGES)
_stage_sets = st.frozensets(_stages)


def marker_tree(track: str, stages: frozenset[str]) -> dict[str, bytes]:
    """Return the files of ``content/<track>/`` that make exactly ``stages`` carry markers."""
    base = f"content/{track}"
    files: dict[str, bytes] = {}
    for stage, names in _JSON_MARKERS.items():
        if stage in stages:
            files.update({f"{base}/{name}": b"[]" for name in names})
    if "outline" in stages or "lessons" in stages:
        lessons = [{"provenance": {"stable_id": lesson}} for lesson in LESSON_IDS]
        files[f"{base}/lessons.json"] = json.dumps(lessons).encode()
    if "lessons" in stages:
        for lesson in LESSON_IDS:
            for locale in ("fr", "en"):
                files[f"{base}/mdx/{lesson}.{locale}.mdx"] = b"body"
    return files


class RecordingGit:
    """In-memory :class:`GitWorkspace` that records every call it receives."""

    def __init__(self, refs: Mapping[str, Mapping[str, bytes]]) -> None:
        self.refs: dict[str, dict[str, bytes]] = {k: dict(v) for k, v in refs.items()}
        self.created: list[str] = []
        self.commits: list[str] = []

    def list_files(self, ref: str, prefix: str = "") -> frozenset[str]:
        return frozenset(p for p in self.refs.get(ref, {}) if p.startswith(prefix))

    def read_text(self, ref: str, path: str) -> str | None:
        data = self.refs.get(ref, {}).get(path)
        return None if data is None else data.decode("utf-8")

    def create_branch(self, name: str, start_point: str) -> None:
        self.created.append(name)
        self.refs[name] = dict(self.refs.get(start_point, {}))

    def commit_files(self, branch: str, files: Mapping[str, bytes], message: str) -> str:
        self.commits.append(branch)
        self.refs[branch].update(files)
        return f"commit{len(self.commits)}"


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"run{self._n}"


class _NoLabs:
    def execute(self, lab: object) -> tuple[()]:
        return ()


class _Rig:
    def __init__(self, git: RecordingGit) -> None:
        clock = Test_Clock()
        self.git = git
        self.ledger = In_Memory_GenerationLedger(clock)
        self.wired = 0
        self.llm = Fake_LLMProvider()
        self.verifier_llm = Fake_LLMProvider()
        self.verifier = VerifyItem(
            independent_llm=self.verifier_llm,
            sandbox=Fake_SandboxProvider(clock),
            lab_verifier=_NoLabs(),
        )
        self.generator = Generator(
            GeneratorDeps(
                git=git,
                ledger=self.ledger,
                clock=clock,
                ids=_Ids(),
                prompts=PromptStore(),
                wire=self._wire,
                base_branch=BASE_BRANCH,
            )
        )

    def _wire(self, scope: JobScope) -> RunProviders:
        self.wired += 1
        return RunProviders(self.llm, self.verifier)

    @property
    def jobs(self) -> int:
        return len(self.ledger._jobs)


def _snapshot(git: RecordingGit, names: tuple[str, ...]) -> dict[str, dict[str, bytes]]:
    return {n: dict(git.refs[n]) for n in names if n in git.refs}


@settings(max_examples=150, deadline=None)
@given(
    track=_slugs,
    stage=_stages,
    merged=_stage_sets,
    named=_stage_sets,
    use_ref=st.booleans(),
    ref_exists=st.booleans(),
)
def test_gate_passes_iff_stage_is_brief_or_predecessor_has_markers(
    track: str,
    stage: str,
    merged: frozenset[str],
    named: frozenset[str],
    use_ref: bool,
    ref_exists: bool,
) -> None:
    """Property 9: proceed iff brief, or the immediate predecessor is merged or on the Base_Ref."""
    refs = {BASE_BRANCH: marker_tree(track, merged)}
    if use_ref and ref_exists:
        refs[NAMED_REF] = marker_tree(track, named)
    git = RecordingGit(refs)
    rig = _Rig(git)
    before = _snapshot(git, (BASE_BRANCH, NAMED_REF))

    outcome = rig.generator.run(
        GenerationRequest(track, stage, base_ref=NAMED_REF if use_ref else None)
    )

    index = STAGES.index(stage)
    predecessor = None if index == 0 else STAGES[index - 1]
    on_ref = use_ref and ref_exists and predecessor in named
    expected_pass = predecessor is None or predecessor in merged or on_ref

    if not expected_pass:
        # Req 5.3, 5.11, 5.12, 5.15: refusal names the missing Stage and does nothing else.
        assert outcome.exit_code is ExitCode.STAGE_GATE
        assert f"{predecessor!r}" in outcome.message
        assert outcome.branch is None and outcome.run_id is None
        assert git.created == [] and git.commits == []
        assert rig.jobs == 0
        assert rig.wired == 0
        assert rig.llm.calls == ()
        assert rig.verifier_llm.calls == ()
    else:
        assert outcome.exit_code is not ExitCode.STAGE_GATE
        assert "preceding stage" not in outcome.message
        # Req 5.4: any branch created holds the track and the Stage and starts the prefix.
        for name in git.created:
            assert name.startswith(f"generate/{track}/{stage}/")

    # Req 5.14, 5.16: neither the merged base nor the named ref is ever changed.
    assert _snapshot(git, (BASE_BRANCH, NAMED_REF)) == before
    assert set(git.commits) <= set(git.created)


@settings(max_examples=100, deadline=None)
@given(
    track=_slugs,
    stage=_stages,
    merged=_stage_sets,
    named=_stage_sets,
    other_merged=_stage_sets,
    other_named=_stage_sets,
)
def test_stages_other_than_the_predecessor_never_change_the_decision(
    track: str,
    stage: str,
    merged: frozenset[str],
    named: frozenset[str],
    other_merged: frozenset[str],
    other_named: frozenset[str],
) -> None:
    """Property 9: swapping every non-predecessor Stage leaves the gate decision unchanged."""
    index = STAGES.index(stage)
    predecessor = None if index == 0 else STAGES[index - 1]

    def rebuilt(source: frozenset[str], donor: frozenset[str]) -> frozenset[str]:
        keep = {s for s in source if s == predecessor}
        return frozenset(keep | {s for s in donor if s != predecessor})

    def refused(merged_set: frozenset[str], named_set: frozenset[str]) -> bool:
        git = RecordingGit(
            {
                BASE_BRANCH: marker_tree(track, merged_set),
                NAMED_REF: marker_tree(track, named_set),
            }
        )
        outcome = _Rig(git).generator.run(GenerationRequest(track, stage, base_ref=NAMED_REF))
        return outcome.exit_code is ExitCode.STAGE_GATE

    assert refused(merged, named) == refused(
        rebuilt(merged, other_merged), rebuilt(named, other_named)
    )


@settings(max_examples=30, deadline=None)
@given(track=_slugs, stage=_stages)
def test_branch_name_contains_track_and_stage_and_is_a_valid_git_reference(
    track: str, stage: str
) -> None:
    """Property 9 / Req 5.4: ``generate/<track>/<stage>/<run>`` is a valid ref name."""
    name = generation_branch_name(track, stage, "run1")
    assert track in name and stage in name
    check = subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{name}"], capture_output=True, check=False
    )
    assert check.returncode == 0
