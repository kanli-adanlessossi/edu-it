"""Git port for the Generator (ai-content Req 5.4, 5.5, 5.9, 5.14, 5.16).

The port is deliberately small. It can list and read files at a ref, create a
Generation_Branch and add a commit to it. It has no method that joins branches
and none that publishes to a remote, so the rule "the Generator never
integrates a Generation_Branch and never writes to a protected branch" holds
by construction (Req 5.9). A static test checks the package for those verbs.

Stage markers are read with ``git ls-tree`` only. A Base_Ref is just another
ref passed to :meth:`GitWorkspace.list_files`: it is a source of file names and
nothing else (Req 5.14, 5.16). Nothing is checked out, so the working tree and
index are never touched; commits are built with plumbing commands against a
temporary index file.

This module holds the port only (no process execution). The adapter over the
``git`` executable lives in ``edu_it_infrastructure.git_workspace``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "BRANCH_PREFIX",
    "BranchExistsError",
    "GitError",
    "GitWorkspace",
    "generation_branch_name",
]

#: Every branch the Generator creates lives under this prefix (Req 5.4).
BRANCH_PREFIX: Final = "generate/"


class GitError(RuntimeError):
    """A git command failed or an argument was refused."""


class BranchExistsError(GitError):
    """The Generation_Branch to create already exists."""


def generation_branch_name(track: str, stage: str, run_id: str) -> str:
    """Return ``generate/<track>/<stage>/<run_id>`` (Req 5.4)."""
    for label, value in (("track", track), ("stage", stage), ("run_id", run_id)):
        if not value or "/" in value or value.startswith(("-", ".")) or ".." in value:
            msg = f"invalid {label} for a branch name: {value!r}"
            raise ValueError(msg)
    return f"{BRANCH_PREFIX}{track}/{stage}/{run_id}"


@runtime_checkable
class GitWorkspace(Protocol):
    """Read files at a ref, create a Generation_Branch, commit to it.

    Intentionally has no method that joins branches or publishes to a remote.
    """

    def list_files(self, ref: str, prefix: str = "") -> frozenset[str]:
        """Return repository paths under ``prefix`` at ``ref`` (``git ls-tree``).

        Returns an empty set when the ref or the prefix does not exist, so a
        missing Base_Ref reads as "no markers" and the gate then refuses.
        """
        ...

    def read_text(self, ref: str, path: str) -> str | None:
        """Return the UTF-8 text of ``path`` at ``ref``, or ``None`` when absent."""
        ...

    def create_branch(self, name: str, start_point: str) -> None:
        """Create ``name`` (under :data:`BRANCH_PREFIX`) at ``start_point``."""
        ...

    def commit_files(self, branch: str, files: Mapping[str, bytes], message: str) -> str:
        """Add one commit with ``files`` on ``branch`` and return its id."""
        ...
