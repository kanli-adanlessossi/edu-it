"""Tests for the Generator's git port and stage markers.

Run against real temporary git repositories.

Validates: Requirements 5.1, 5.4, 5.5, 5.9, 5.14, 5.16
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
from pathlib import Path

import edu_it_domain.ports.git_workspace as git_port_module
import edu_it_infrastructure.git_workspace as git_adapter_module
import edu_it_worker.ai.stages as stages_module
import pytest
from edu_it_domain.ai.generation import missing_stage
from edu_it_domain.ports.git_workspace import (
    BranchExistsError,
    GitError,
    GitWorkspace,
    generation_branch_name,
)
from edu_it_infrastructure.git_workspace import SubprocessGitWorkspace
from edu_it_worker.ai.stages import committed_units, mdx_path, stages_with_markers

TRACK = "networking-basics"
BASE = f"content/{TRACK}"


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
    _write(tmp_path, f"{BASE}/brief.json", "{}\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def _outline(repo: Path, lessons: tuple[str, ...] = ("lesson.a", "lesson.b")) -> None:
    for name in ("tracks", "modules", "concepts", "prerequisites"):
        _write(repo, f"{BASE}/{name}.json", "[]\n")
    _write(
        repo,
        f"{BASE}/lessons.json",
        json.dumps([{"provenance": {"stable_id": s}} for s in lessons]),
    )


def test_protocol_has_no_join_or_publish_methods(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    assert isinstance(ws, GitWorkspace)
    methods = {
        n for n, _ in inspect.getmembers(GitWorkspace, inspect.isfunction) if n != "__init__"
    }
    assert methods == {"list_files", "read_text", "create_branch", "commit_files"}


def test_worker_ai_package_has_no_integration_verbs_or_protected_names() -> None:
    for module in (git_port_module, git_adapter_module, stages_module):
        source = inspect.getsource(module)
        assert not re.search(r"\b(merge|push|rebase|checkout)\b", source, re.IGNORECASE)
        assert not re.search(r"\b(main|master)\b", source)


def test_markers_come_from_ls_tree_of_base_and_of_named_ref(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    assert stages_with_markers(ws, "trunk", TRACK) == {"brief"}

    # Outline exists only on a side branch (the Base_Ref).
    ws.create_branch(generation_branch_name(TRACK, "outline", "r1"), "trunk")
    side = generation_branch_name(TRACK, "outline", "r1")
    files = {
        f"{BASE}/{n}.json": b"[]\n" for n in ("tracks", "modules", "concepts", "prerequisites")
    }
    files[f"{BASE}/lessons.json"] = b'[{"provenance": {"stable_id": "lesson.a"}}]'
    ws.commit_files(side, files, "outline")

    base_stages = stages_with_markers(ws, "trunk", TRACK)
    ref_stages = stages_with_markers(ws, side, TRACK)
    assert "outline" not in base_stages
    assert "outline" in ref_stages
    # The ref widens where markers are found; the gate itself is unchanged.
    assert missing_stage("lessons", base_stages) == "outline"
    assert missing_stage("lessons", base_stages, ref_stages) is None
    # Nothing was joined into the base and the working tree is untouched.
    assert "outline" not in stages_with_markers(ws, "trunk", TRACK)
    assert not (repo / BASE / "tracks.json").exists()


def test_unknown_base_ref_reads_as_no_markers(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    assert ws.list_files("no-such-ref", f"{BASE}/") == frozenset()
    assert stages_with_markers(ws, "no-such-ref", TRACK) == frozenset()


def test_lessons_markers_need_fr_and_en_for_every_outline_lesson(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    branch = generation_branch_name(TRACK, "lessons", "r1")
    _outline(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "outline")
    ws.create_branch(branch, "trunk")
    files = {mdx_path(TRACK, "lesson.a", "fr"): b"a\n", mdx_path(TRACK, "lesson.a", "en"): b"a\n"}
    files[mdx_path(TRACK, "lesson.b", "en")] = b"b\n"
    ws.commit_files(branch, files, "partial")
    assert "lessons" not in stages_with_markers(ws, branch, TRACK)
    assert committed_units(ws, branch, TRACK, "lessons") == {"lesson.a"}

    ws.commit_files(branch, {mdx_path(TRACK, "lesson.b", "fr"): b"b\n"}, "rest")
    assert "lessons" in stages_with_markers(ws, branch, TRACK)
    assert committed_units(ws, branch, TRACK, "lessons") == {"lesson.a", "lesson.b"}


def test_committed_units_for_item_stages_and_single_unit_stages(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    _write(repo, f"{BASE}/quizzes.json", json.dumps([{"provenance": {"stable_id": "quiz.x"}}]))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "q")
    assert committed_units(ws, "trunk", TRACK, "quizzes") == {"quiz.x"}
    assert committed_units(ws, "trunk", TRACK, "labs") == frozenset()
    assert committed_units(ws, "trunk", TRACK, "brief") == {f"brief.{TRACK}"}
    assert committed_units(ws, "trunk", TRACK, "outline") == frozenset()


def test_commit_adds_a_commit_without_touching_working_tree_or_other_refs(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    branch = generation_branch_name(TRACK, "lessons", "r2")
    ws.create_branch(branch, "trunk")
    before = _git(repo, "rev-parse", "trunk")
    commit = ws.commit_files(branch, {f"{BASE}/new.json": b"[]\n"}, "add")
    assert _git(repo, "rev-parse", branch) == commit
    assert _git(repo, "rev-parse", "trunk") == before
    assert _git(repo, "rev-parse", f"{commit}^") == before
    assert ws.read_text(branch, f"{BASE}/new.json") == "[]\n"
    assert ws.read_text("trunk", f"{BASE}/new.json") is None
    assert not (repo / BASE / "new.json").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_branch_rules(repo: Path) -> None:
    ws = SubprocessGitWorkspace(repo)
    name = generation_branch_name(TRACK, "brief", "r3")
    assert name == f"generate/{TRACK}/brief/r3"
    ws.create_branch(name, "trunk")
    with pytest.raises(BranchExistsError):
        ws.create_branch(name, "trunk")
    with pytest.raises(GitError):
        ws.create_branch("trunk-copy", "trunk")
    with pytest.raises(GitError):
        ws.commit_files("trunk", {f"{BASE}/x.json": b"x"}, "no")
    with pytest.raises(GitError):
        ws.commit_files(name, {"../escape": b"x"}, "no")
    with pytest.raises(ValueError):
        generation_branch_name("a/b", "brief", "r")
