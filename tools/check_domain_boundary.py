#!/usr/bin/env python3
"""Domain import-boundary check (Req 1.8).

Statically assert that the framework-free shared domain package
(``packages/domain``) imports no web or API framework. The domain is the
inner layer of the ``api -> application -> domain <- infrastructure``
layering; it must stay free of web/API framework dependencies so it can be
reused by ``apps/api`` and ``apps/worker`` and tested without any real
infrastructure (see ``.kiro/steering/structure.md`` and
``.kiro/steering/tech.md``).

This is a self-contained check that uses only the Python standard library
(``ast``), so it introduces no new pinned tool dependency. It parses every
``.py`` file under ``packages/domain`` and inspects each ``import`` and
``from ... import`` statement. If the top-level module of any import is a
banned web or API framework, the check prints the offending file path, line,
and banned import, and exits non-zero. When the domain is clean it exits zero.

Usage::

    python tools/check_domain_boundary.py [DOMAIN_DIR]

``DOMAIN_DIR`` defaults to ``packages/domain`` relative to the repository
root (the parent directory of this ``tools/`` folder).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Banned top-level module names: web and API frameworks the framework-free
# domain must never import (Req 1.8). Scoped to web/API frameworks per the
# requirement -- web UI frameworks, the API/web server frameworks, their
# validation-as-a-framework dependency, and the HTTP/web client and
# datastore-client frameworks a framework-free domain must not reach for.
BANNED_MODULES: frozenset[str] = frozenset(
    {
        # API / web server frameworks (apps/api, apps/worker boundary layers)
        "fastapi",
        "starlette",
        "pydantic",  # used as the API framework's validation layer
        "flask",
        "django",
        "sanic",
        "aiohttp",
        "tornado",
        "uvicorn",
        "gunicorn",
        "hypercorn",
        # Web UI frameworks (apps/web)
        "next",
        "react",
        # HTTP / web client frameworks
        "httpx",
        "requests",
        "aiohttp_client",
        # Datastore / driver frameworks (infrastructure concerns)
        "sqlalchemy",
        "alembic",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "redis",
        "aioredis",
    }
)


class Violation:
    """A single banned import found in the domain package."""

    __slots__ = ("file", "line", "module", "statement")

    def __init__(self, file: Path, line: int, module: str, statement: str) -> None:
        self.file = file
        self.line = line
        self.module = module
        self.statement = statement

    def render(self, root: Path) -> str:
        try:
            shown = self.file.relative_to(root)
        except ValueError:
            shown = self.file
        return (
            f"{shown}:{self.line}: banned import of web/API framework "
            f"'{self.module}' ({self.statement})"
        )


def _top_level(module: str) -> str:
    """Return the first dotted component of a module path."""
    return module.split(".", 1)[0]


def _scan_file(path: Path) -> list[Violation]:
    """Parse one Python file and return any banned-import violations."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - defensive
        # A syntactically invalid domain file is itself a problem; surface it
        # as a violation rather than silently passing.
        return [
            Violation(
                path,
                exc.lineno or 0,
                "<syntax-error>",
                f"could not parse: {exc.msg}",
            )
        ]

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_level(alias.name)
                if top in BANNED_MODULES:
                    violations.append(Violation(path, node.lineno, top, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            # Ignore relative imports (node.level > 0): they stay within the
            # domain package and can never reach an external framework.
            if node.level and not node.module:
                continue
            if node.module is None:
                continue
            top = _top_level(node.module)
            if node.level == 0 and top in BANNED_MODULES:
                names = ", ".join(a.name for a in node.names)
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        top,
                        f"from {node.module} import {names}",
                    )
                )
    return violations


def check(domain_dir: Path) -> list[Violation]:
    """Scan every ``.py`` file under ``domain_dir`` for banned imports."""
    violations: list[Violation] = []
    for path in sorted(domain_dir.rglob("*.py")):
        violations.extend(_scan_file(path))
    return violations


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    domain_dir = Path(argv[1]).resolve() if len(argv) > 1 else repo_root / "packages" / "domain"

    if not domain_dir.exists():
        print(
            f"domain-boundary: directory not found: {domain_dir}",
            file=sys.stderr,
        )
        return 2

    violations = check(domain_dir)
    if violations:
        print(
            f"domain-boundary: FAIL - {len(violations)} banned web/API "
            f"framework import(s) in {domain_dir}:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v.render(repo_root)}", file=sys.stderr)
        return 1

    print(f"domain-boundary: OK - no web/API framework imports in {domain_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
