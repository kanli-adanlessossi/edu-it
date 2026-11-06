#!/usr/bin/env python3
"""Migration backward-compatibility check (kubernetes-deployment, Req 21.6).

Migrations stay backward compatible with the previous release for one release, so
the previous Api and Worker keep working during and after a failed upgrade. A
release may therefore only *add* schema. Dropping or renaming a column or a table
in ``upgrade()`` breaks the previous release, which still reads it; it is allowed
only in the following release, once nothing uses the object any more.

This check statically inspects the ``upgrade()`` function of Alembic migration
files (and the module-level helpers it calls). ``downgrade()`` is ignored: it
legitimately drops what ``upgrade()`` added. It reports:

* ``op.drop_column`` / ``op.drop_table`` / ``batch.drop_column`` (any receiver),
* ``op.rename_table`` and ``alter_column(..., new_column_name=...)``,
* raw SQL passed to ``execute`` that contains ``DROP TABLE``, ``DROP COLUMN`` or
  ``RENAME``.

A finding is accepted when its line (or the line above) carries the marker
``# migration-compat: allow`` followed by a reason; use it only in the release
after the one that stopped using the object.

Usage::

    python tools/check_migrations_compat.py --base <ref> [--repo DIR] [--versions-dir DIR]
    python tools/check_migrations_compat.py FILE [FILE ...]

With ``--base`` the files checked are the migration files added or modified
between the merge base of ``<ref>`` and ``HEAD`` (this release). Exit codes: 0
clean, 1 violations, 2 usage or git error. Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Finding", "GitError", "changed_migration_files", "find_incompatible_operations", "main"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSIONS_DIR = (
    "packages/infrastructure/edu_it_infrastructure/persistence/migrations/versions"
)
ALLOW_MARKER = "migration-compat: allow"

_DROP_CALLS = {"drop_column": "drops a column", "drop_table": "drops a table"}
_RENAME_CALLS = {"rename_table": "renames a table"}
_RAW_SQL = re.compile(r"\bDROP\s+(?:TABLE|COLUMN)\b|\bRENAME\b", re.IGNORECASE)


class GitError(RuntimeError):
    """A git command failed (unknown ref, not a repository, missing history)."""


@dataclass(frozen=True, order=True)
class Finding:
    """One backward-incompatible operation in an ``upgrade()`` body."""

    line: int
    operation: str
    reason: str

    def render(self) -> str:
        return f"line {self.line}: {self.operation} {self.reason}"


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _string_args(node: ast.Call) -> list[str]:
    """String constants in the call arguments, including inside ``sa.text(...)``."""
    found: list[str] = []
    for arg in [*node.args, *(kw.value for kw in node.keywords)]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.append(sub.value)
    return found


def _classify(node: ast.Call) -> tuple[str, str] | None:
    name = _call_name(node)
    if name is None:
        return None
    if name in _DROP_CALLS:
        return name, _DROP_CALLS[name]
    if name in _RENAME_CALLS:
        return name, _RENAME_CALLS[name]
    if name == "alter_column" and any(kw.arg == "new_column_name" for kw in node.keywords):
        return name, "renames a column"
    if name == "execute":
        for text in _string_args(node):
            match = _RAW_SQL.search(text)
            if match:
                return "execute", f"runs raw SQL containing {match.group(0).upper()!r}"
    return None


def _called_names(func: ast.FunctionDef) -> set[str]:
    calls = (n for n in ast.walk(func) if isinstance(n, ast.Call))
    return {c.func.id for c in calls if isinstance(c.func, ast.Name)}


def _upgrade_scope(tree: ast.Module) -> list[ast.FunctionDef]:
    """``upgrade`` plus the module-level functions it reaches by name."""
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "upgrade" not in funcs:
        return []
    seen: dict[str, ast.FunctionDef] = {}
    todo = ["upgrade"]
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen[name] = funcs[name]
        todo.extend(_called_names(funcs[name]))
    return list(seen.values())


def find_incompatible_operations(source: str) -> list[Finding]:
    """Return the drops and renames in the ``upgrade()`` path of one migration.

    Pure: takes the migration source text, returns findings sorted by line. A
    finding whose line or preceding line carries the allow marker is skipped.
    Source that does not parse raises ``SyntaxError``.
    """
    tree = ast.parse(source)
    lines = source.splitlines()

    def allowed(lineno: int) -> bool:
        return any(
            0 < n <= len(lines) and ALLOW_MARKER in lines[n - 1] for n in (lineno, lineno - 1)
        )

    findings: set[Finding] = set()
    for func in _upgrade_scope(tree):
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            hit = _classify(node)
            if hit and not allowed(node.lineno):
                findings.add(Finding(node.lineno, *hit))
    return sorted(findings)


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


def changed_migration_files(
    repo: Path, base: str, versions_dir: str = DEFAULT_VERSIONS_DIR
) -> list[Path]:
    """Migration files added or modified since the merge base of ``base`` and HEAD."""
    out = _git(
        repo, "diff", "--name-only", "--diff-filter=AMR", "-z", f"{base}...HEAD", "--", versions_dir
    )
    names = [n for n in out.split("\0") if n.endswith(".py") and Path(n).name != "__init__.py"]
    return sorted(repo / n for n in names)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("files", nargs="*", type=Path, help="migration files to check")
    parser.add_argument("--base", help="git ref; check migrations changed since its merge base")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--versions-dir", default=DEFAULT_VERSIONS_DIR)
    args = parser.parse_args(argv)

    if bool(args.files) == bool(args.base):
        parser.error("give either --base <ref> or one or more migration files")

    try:
        files = args.files or changed_migration_files(args.repo, args.base, args.versions_dir)
    except GitError as exc:
        print(f"migrations-compat: ERROR - {exc}", file=sys.stderr)
        return 2

    violations: list[tuple[Path, Finding]] = []
    for path in files:
        try:
            findings = find_incompatible_operations(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            print(f"migrations-compat: ERROR - cannot check {path}: {exc}", file=sys.stderr)
            return 2
        violations.extend((path, f) for f in findings)

    if violations:
        print(
            f"migrations-compat: FAIL - {len(violations)} drop/rename operation(s) in upgrade(); "
            "the previous release still uses them, so move them to the following release:",
            file=sys.stderr,
        )
        for path, finding in violations:
            print(f"  {path}: {finding.render()}", file=sys.stderr)
        return 1

    print(f"migrations-compat: OK - {len(files)} migration file(s) are add-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
