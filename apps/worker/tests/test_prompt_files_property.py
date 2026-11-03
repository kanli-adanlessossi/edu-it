"""Property test: prompt files are named by id and version and never edited.

Covers the file-name round trip of the prompt store and the immutability check
(``tools/check_prompt_immutability.py``) over generated base and head sets of
prompt files, using real temporary git repositories.

Feature: ai-content, Property 8: Prompt files are named by id and version and never edited

Validates: Requirements 4.1, 4.2, 4.3
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
from pathlib import Path

from check_prompt_immutability import changed_existing_files, main
from edu_it_worker.ai.prompt_store import PromptStore
from hypothesis import given, settings
from hypothesis import strategies as st

_ids = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)
_versions = st.integers(min_value=1, max_value=99).map(str)
_pairs = st.tuples(_ids, _versions)
_ACTIONS = ("keep", "modify", "delete", "rename")


def _front(prompt_id: str, version: str, text: str) -> str:
    body = f"{prompt_id} v{version} {text}\n" * 5
    return f"---\noutput_schema: none\ninput_kind: none\nincludes:\n---\n{body}"


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
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _put(repo: Path, name: str, text: str) -> None:
    path = repo / "prompts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _name(prompt_id: str, version: str) -> str:
    return f"{prompt_id}__v{version}.md"


@settings(max_examples=100, deadline=None)
@given(pair=_pairs)
def test_file_name_round_trips_through_the_store(pair: tuple[str, str]) -> None:
    """Parsing the file name built from an id and version returns them (Req 4.1)."""
    prompt_id, version = pair
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / _name(prompt_id, version)).write_bytes(
            _front(prompt_id, version, "x").encode("utf-8")
        )
        store = PromptStore(root)
        assert store.available() == ((prompt_id, version),)
        loaded = store.load(prompt_id, version)
        assert (loaded.prompt_id, loaded.prompt_version) == (prompt_id, version)


@settings(max_examples=20, deadline=None)
@given(
    base=st.lists(_pairs, min_size=1, max_size=5, unique=True),
    new=st.lists(st.tuples(_ids, st.integers(min_value=100, max_value=199).map(str)), max_size=2),
    data=st.data(),
)
def test_immutability_check_fails_exactly_for_changed_existing_files(
    base: list[tuple[str, str]],
    new: list[tuple[str, str]],
    data: st.DataObject,
) -> None:
    """Fails iff a base file is modified, renamed or deleted; names exactly those (Req 4.2, 4.3)."""
    actions = [data.draw(st.sampled_from(_ACTIONS)) for _ in base]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = Path(tmp)
        _git(repo, "init", "-q", "-b", "main")
        for prompt_id, version in base:
            _put(repo, _name(prompt_id, version), _front(prompt_id, version, "base"))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        _git(repo, "checkout", "-q", "-b", "feature")

        expected: set[str] = set()
        for (prompt_id, version), action in zip(base, actions, strict=True):
            name = _name(prompt_id, version)
            file = repo / "prompts" / name
            if action == "modify":
                _put(repo, name, _front(prompt_id, version, "base") + "edited\n")
            elif action == "delete":
                file.unlink()
            elif action == "rename":
                file.rename(repo / "prompts" / _name(prompt_id, str(int(version) + 1000)))
            if action != "keep":
                expected.add(f"prompts/{name}")
        for prompt_id, version in new:
            _put(repo, _name(prompt_id, version), _front(prompt_id, version, "new"))

        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "--allow-empty", "-m", "head")

        reported = {rel for rel, _status in changed_existing_files(repo, "main")}
        assert reported == expected

        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["--base", "main", "--repo", str(repo)])
        assert code == (1 if expected else 0)
        for rel in expected:
            assert rel in err.getvalue()
        for prompt_id, version in base:
            rel = f"prompts/{_name(prompt_id, version)}"
            if rel not in expected:
                assert rel not in err.getvalue()
