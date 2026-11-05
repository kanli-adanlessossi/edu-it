#!/usr/bin/env python3
"""CI action pinning check (kubernetes-deployment, Req 15.11).

A third-party action referenced by a mutable tag or branch can change under us, so
every ``uses:`` reference to a third-party action or reusable workflow must be
pinned to a full 40-character hexadecimal commit SHA (``owner/repo@<sha>``).

Reported: every ``uses:`` value that is not local and whose ref is not a 40-character
hexadecimal commit SHA (tags, branches, short SHAs, missing ref, ``docker://`` images).
Not reported: local references (``./path``) and commented-out lines. A trailing
``# v4.1.0`` comment after a pinned SHA is fine and ignored.

Usage::

    python tools/check_ci_pins.py [--repo DIR] [FILE ...]

With no files, scans ``.github/workflows/*.yml|yaml`` and
``.github/actions/**/action.yml|yaml`` under the repository. Exit codes: 0 clean,
1 unpinned references, 2 usage or read error. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Finding", "find_unpinned_actions", "default_files", "main"]

REPO_ROOT = Path(__file__).resolve().parents[1]

_USES = re.compile(r"""^\s*(?:-\s+)*uses\s*:\s*(?P<value>.*)$""")
_SHA = re.compile(r"[0-9a-fA-F]{40}")


@dataclass(frozen=True, order=True)
class Finding:
    """One third-party ``uses:`` reference that is not pinned to a commit SHA."""

    line: int
    reference: str

    def render(self) -> str:
        return f"line {self.line}: {self.reference!r} is not pinned to a 40-character commit SHA"


def _clean_value(raw: str) -> str:
    """Strip quotes and a trailing YAML comment from a ``uses:`` value."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _is_pinned(reference: str) -> bool:
    if "@" not in reference:
        return False
    ref = reference.rsplit("@", 1)[1]
    return _SHA.fullmatch(ref) is not None


def find_unpinned_actions(text: str) -> list[Finding]:
    """Return the third-party ``uses:`` references in one workflow that are not SHA-pinned.

    Pure: takes the workflow (or composite action) text, returns findings sorted by line.
    """
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _USES.match(line)
        if not match:
            continue
        reference = _clean_value(match.group("value"))
        if not reference or reference.startswith("./"):
            continue
        if not _is_pinned(reference):
            findings.append(Finding(number, reference))
    return sorted(findings)


def default_files(repo: Path) -> list[Path]:
    """Workflow files and composite actions under ``.github``."""
    github = repo / ".github"
    files: set[Path] = set()
    for pattern in ("workflows/*.yml", "workflows/*.yaml", "actions/**/action.yml", "actions/**/action.yaml"):
        files.update(p for p in github.glob(pattern) if p.is_file())
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("files", nargs="*", type=Path, help="workflow files to check")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    args = parser.parse_args(argv)

    files = args.files or default_files(args.repo)

    violations: list[tuple[Path, Finding]] = []
    for path in files:
        try:
            findings = find_unpinned_actions(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            print(f"ci-pins: ERROR - cannot read {path}: {exc}", file=sys.stderr)
            return 2
        violations.extend((path, f) for f in findings)

    if violations:
        print(
            f"ci-pins: FAIL - {len(violations)} third-party action reference(s) "
            "not pinned to a full commit SHA:",
            file=sys.stderr,
        )
        for path, finding in violations:
            print(f"  {path}: {finding.render()}", file=sys.stderr)
        return 1

    print(f"ci-pins: OK - {len(files)} file(s), all third-party actions pinned by SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
