"""Tests for the prompt immutability check (tools/check_prompt_immutability.py).

Uses real temporary git repositories.

**Validates: Requirements 4.2, 4.3**
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from check_prompt_immutability import changed_existing_files, main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
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
    )


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for name in ("keep", "edit", "move", "drop"):
        _write(
            tmp_path, f"prompts/{name}__v1.md", f"{name} prompt text, long enough to match\n" * 5
        )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


def test_new_version_file_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "prompts/edit__v2.md", "new text\n")
    _write(repo, "docs/other.md", "unrelated\n")
    _commit(repo)
    assert changed_existing_files(repo, "main") == []
    assert main(["--base", "main", "--repo", str(repo)]) == 0
    assert "OK" in capsys.readouterr().out


def test_modified_renamed_deleted_are_all_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "prompts/edit__v1.md", "changed\n")
    (repo / "prompts/move__v1.md").rename(repo / "prompts/move__v2.md")
    (repo / "prompts/drop__v1.md").unlink()
    _write(repo, "prompts/fresh__v1.md", "brand new\n")
    _commit(repo)

    assert changed_existing_files(repo, "main") == [
        ("prompts/drop__v1.md", "deleted"),
        ("prompts/edit__v1.md", "modified"),
        ("prompts/move__v1.md", "renamed"),
    ]
    assert main(["--base", "main", "--repo", str(repo)]) == 1
    err = capsys.readouterr().err
    for name in ("drop__v1", "edit__v1", "move__v1"):
        assert name in err
    assert "keep__v1" not in err
    assert "fresh__v1" not in err


def test_changes_merged_into_base_after_branch_point_are_ignored(repo: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    _write(repo, "prompts/edit__v1.md", "edited on main\n")
    _commit(repo)
    _git(repo, "checkout", "-q", "feature")
    _write(repo, "prompts/edit__v2.md", "v2\n")
    _commit(repo)
    assert changed_existing_files(repo, "main") == []


def test_unknown_base_is_a_usage_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--base", "no-such-ref", "--repo", str(repo)]) == 2
    assert "ERROR" in capsys.readouterr().err
