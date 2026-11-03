"""Subprocess adapter for the :class:`GitWorkspace` port (ai-content Req 5.4-5.16).

Nothing is checked out, so the working tree and index are never touched;
commits are built with plumbing commands against a temporary index file. The
adapter has no operation that joins branches or publishes to a remote. Every
``git`` call uses an argument list, never a shell.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from edu_it_domain.ports.git_workspace import BRANCH_PREFIX, BranchExistsError, GitError

__all__ = ["SubprocessGitWorkspace"]

_DEFAULT_AUTHOR_NAME: Final = "edu-it-generator"
_DEFAULT_AUTHOR_EMAIL: Final = "generator@edu-it.invalid"
_REGULAR_FILE_MODE: Final = "100644"


def _check_ref(ref: str) -> None:
    if not ref or ref.startswith("-") or any(c in ref for c in "\x00\n\r"):
        msg = f"invalid ref: {ref!r}"
        raise GitError(msg)


def _check_path(path: str) -> None:
    parts = path.split("/")
    if not path or path.startswith("/") or any(p in {"", ".", "..", ".git"} for p in parts):
        msg = f"invalid repository path: {path!r}"
        raise GitError(msg)


class SubprocessGitWorkspace:
    """:class:`GitWorkspace` over the ``git`` executable."""

    def __init__(
        self,
        repo: Path,
        *,
        author_name: str = _DEFAULT_AUTHOR_NAME,
        author_email: str = _DEFAULT_AUTHOR_EMAIL,
    ) -> None:
        self._repo = repo
        self._author_name = author_name
        self._author_email = author_email

    # -- plumbing -----------------------------------------------------------

    def _run(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        full_env = {**os.environ, **(env or {})}
        result = subprocess.run(
            ["git", "-C", str(self._repo), "-c", "core.autocrlf=false", *args],
            input=stdin,
            capture_output=True,
            env=full_env,
            check=False,
        )
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            msg = f"git {args[0]} failed: {detail}"
            raise GitError(msg)
        return result

    def _rev(self, ref: str) -> str | None:
        result = self._run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else None

    # -- reads --------------------------------------------------------------

    def list_files(self, ref: str, prefix: str = "") -> frozenset[str]:
        _check_ref(ref)
        if self._rev(ref) is None:
            return frozenset()
        args = ["ls-tree", "-r", "-z", "--name-only", ref]
        if prefix:
            _check_path(prefix.rstrip("/"))
            args += ["--", prefix]
        result = self._run(*args, check=False)
        if result.returncode != 0:
            return frozenset()
        names = result.stdout.decode("utf-8").split("\x00")
        return frozenset(name for name in names if name)

    def read_text(self, ref: str, path: str) -> str | None:
        _check_ref(ref)
        _check_path(path)
        if path not in self.list_files(ref, path):
            return None
        result = self._run("cat-file", "blob", f"{ref}:{path}")
        return result.stdout.decode("utf-8")

    def default_branch(self) -> str | None:
        """Return the integration branch new Generation_Branches start from.

        Read-only: the remote's default (``origin/HEAD``) when it is known, then the
        ``init.defaultBranch`` setting when that branch exists. ``None`` when neither
        gives one, so the caller can ask for an explicit setting.
        """
        head = self._run("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
        if head.returncode == 0:
            name = head.stdout.decode().strip().removeprefix("origin/")
            if name and self._rev(f"refs/heads/{name}") is not None:
                return name
        configured = self._run("config", "--get", "init.defaultBranch", check=False)
        name = configured.stdout.decode().strip() if configured.returncode == 0 else ""
        if name and self._rev(f"refs/heads/{name}") is not None:
            return name
        return None

    # -- writes (new branch and commits only) ---------------------------------

    def create_branch(self, name: str, start_point: str) -> None:
        if not name.startswith(BRANCH_PREFIX) or name.endswith("/") or ".." in name:
            msg = f"Generation_Branch must be under {BRANCH_PREFIX!r}: {name!r}"
            raise GitError(msg)
        _check_ref(start_point)
        if self._rev(f"refs/heads/{name}") is not None:
            msg = f"branch already exists: {name}"
            raise BranchExistsError(msg)
        if self._rev(start_point) is None:
            msg = f"unknown start point: {start_point}"
            raise GitError(msg)
        self._run("branch", "--no-track", name, start_point)

    def commit_files(self, branch: str, files: Mapping[str, bytes], message: str) -> str:
        if not branch.startswith(BRANCH_PREFIX):
            msg = f"commits are only made on {BRANCH_PREFIX!r} branches: {branch!r}"
            raise GitError(msg)
        for path in files:
            _check_path(path)
        ref = f"refs/heads/{branch}"
        parent = self._rev(ref)
        if parent is None:
            msg = f"unknown branch: {branch}"
            raise GitError(msg)
        identity = {
            "GIT_AUTHOR_NAME": self._author_name,
            "GIT_AUTHOR_EMAIL": self._author_email,
            "GIT_COMMITTER_NAME": self._author_name,
            "GIT_COMMITTER_EMAIL": self._author_email,
        }
        with tempfile.TemporaryDirectory(prefix="edu-it-index-") as tmp:
            env = {**identity, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self._run("read-tree", parent, env=env)
            for path, data in sorted(files.items()):
                blob = self._run("hash-object", "-w", "--stdin", stdin=data).stdout.decode().strip()
                self._run(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{_REGULAR_FILE_MODE},{blob},{path}",
                    env=env,
                )
            tree = self._run("write-tree", env=env).stdout.decode().strip()
            commit = (
                self._run("commit-tree", tree, "-p", parent, "-m", message, env=env)
                .stdout.decode()
                .strip()
            )
        self._run("update-ref", ref, commit, parent)
        return commit
