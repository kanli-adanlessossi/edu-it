#!/usr/bin/env python3
"""Prompt immutability check (ai-content, Req 4.2, 4.3).

A Prompt under ``prompts/`` is immutable once committed: a text change needs a
new ``prompt_version`` file. This check diffs ``prompts/`` between a base ref
and ``HEAD`` (against their merge base, so work merged into the base after the
branch point is not blamed on the branch) and fails naming every file that
already existed in the base and is now modified, renamed (reported under its
old name), deleted or changed in type. Newly added files pass.

Usage::

    python tools/check_prompt_immutability.py --base <ref> [--repo DIR] [--prompts-dir prompts]

Exit codes: 0 clean, 1 violations, 2 usage or git error (for example an unknown
base ref or a shallow clone without the base). Stdlib only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

__all__ = ["GitError", "changed_existing_files", "main"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS_DIR = "prompts"


class GitError(RuntimeError):
    """A git command failed (unknown ref, not a repository, missing history)."""


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def changed_existing_files(
    repo: Path, base: str, prompts_dir: str = DEFAULT_PROMPTS_DIR
) -> list[tuple[str, str]]:
    """Return ``(path, status)`` for each prompt file of ``base`` that changed.

    ``path`` is the name the file has in the base. Added files are not returned.
    """
    out = _git(
        repo,
        "diff",
        "--name-status",
        "-z",
        "-M",
        f"{base}...HEAD",
        "--",
        prompts_dir,
    )
    fields = out.split("\0")
    result: list[tuple[str, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        letter = status[0]
        if letter in ("R", "C"):
            old = fields[i + 1]
            i += 3
            if letter == "R":
                result.append((old, "renamed"))
            continue
        path = fields[i + 1]
        i += 2
        if letter == "A":
            continue
        result.append((path, {"M": "modified", "D": "deleted"}.get(letter, "changed")))
    return sorted(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", required=True, help="git ref to diff against")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--prompts-dir", default=DEFAULT_PROMPTS_DIR)
    args = parser.parse_args(argv)

    try:
        violations = changed_existing_files(args.repo, args.base, args.prompts_dir)
    except GitError as exc:
        print(f"prompt-immutability: ERROR - {exc}", file=sys.stderr)
        return 2

    if violations:
        print(
            f"prompt-immutability: FAIL - {len(violations)} existing prompt file(s) "
            f"changed since {args.base}; add a new prompt_version file instead:",
            file=sys.stderr,
        )
        for path, status in violations:
            print(f"  {path}: {status}", file=sys.stderr)
        return 1

    print(f"prompt-immutability: OK - no existing prompt file changed since {args.base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
