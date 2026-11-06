#!/usr/bin/env python3
"""Documents check (kubernetes-deployment, Req 17.6, 17.8, 18.2, 18.5).

`docs/DEPLOYMENT.md` must carry the Go_Live_Checklist with one checkbox item for
each of the required security checks (Req 17.6, 17.8), and every Markdown link in
a checked document must resolve to a file that exists in the repository (Req
18.5). `README.md` must carry exactly one French section of no more than 300
words (Req 18.2).

This module is pure where it matters: ``find_missing_checklist_items``,
``find_broken_links`` and ``french_section_word_count`` take already-read text
(plus, for links, the repository root used to resolve relative paths) and return
plain data. ``main`` does the file I/O and argument handling.

Usage::

    python tools/check_docs.py [--repo DIR]

Checks `docs/DEPLOYMENT.md` for the Go_Live_Checklist items and (together with
`README.md`) for broken links, and `README.md` for the French section word
count. Exit codes: 0 clean, 1 violations, 2 usage or read error. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

__all__ = [
    "GO_LIVE_CHECKLIST_ITEMS",
    "LinkFinding",
    "extract_french_section",
    "find_broken_links",
    "find_go_live_blocking_gaps",
    "find_missing_checklist_items",
    "french_section_word_count",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[1]

DEPLOYMENT_GUIDE = "docs/DEPLOYMENT.md"
README = "README.md"

#: One separate Go_Live_Checklist item is required for each of these checks
#: (Req 17.8), plus the real-Target RuntimeClass item (Req 17.7, folded into
#: "RuntimeClass" below since both name the same control). Matching is by
#: case-insensitive substring so the guide's wording is not pinned verbatim.
GO_LIVE_CHECKLIST_ITEMS: tuple[str, ...] = (
    "networkpolicy enforcement",
    "external secrets",
    "image signature verification",
    "tls",
    "websocket timeouts",
    "rbac review",
    "psa labels",
    "resourcequota values",
    "runc warning",
    "runtimeclass",
)

#: Maximum word count for the README's French section (Req 18.2).
FRENCH_SECTION_MAX_WORDS = 300

_CHECKLIST_ITEM = re.compile(r"^\s*-\s*\[[ xX]\]\s*(?P<text>.+)$")
_MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True, order=True)
class LinkFinding:
    """One Markdown link whose target does not resolve."""

    line: int
    target: str

    def render(self) -> str:
        return f"line {self.line}: link target {self.target!r} does not resolve"


def _checklist_items(text: str) -> list[str]:
    """Checkbox item text found anywhere in the document, lower-cased."""
    items = []
    for line in text.splitlines():
        match = _CHECKLIST_ITEM.match(line)
        if match:
            items.append(match.group("text").strip().lower())
    return items


def find_missing_checklist_items(
    text: str, required: tuple[str, ...] = GO_LIVE_CHECKLIST_ITEMS
) -> list[str]:
    """Return the required checklist phrases with no matching checkbox item.

    Pure: takes the Deployment_Guide text, returns the required phrases (in the
    order given) that no checkbox item (``- [ ]`` / ``- [x]``) contains as a
    case-insensitive substring.
    """
    items = _checklist_items(text)
    return [phrase for phrase in required if not any(phrase in item for item in items)]


def _slug(heading: str) -> str:
    """GitHub-style anchor of a heading: lower-case, punctuation dropped, spaces to hyphens."""
    kept = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return kept.replace(" ", "-")


def find_go_live_blocking_gaps(text: str) -> list[str]:
    """Problems with the "go-live is blocked" rule of the Go_Live_Checklist (Req 17.10).

    Pure: the guide must state that go-live is blocked until a failing item passes, and every
    checkbox item must name its corrective section as a ``Fix:`` clause holding an in-page
    link whose anchor matches a heading of the guide.
    """
    problems: list[str] = []
    if not re.search(r"go-live is blocked until", text, re.IGNORECASE):
        problems.append("no statement that go-live is blocked until a failing item passes")
    anchors = {_slug(m.group(2)) for line in text.splitlines() if (m := _HEADING.match(line))}
    for item in _checklist_items(text):
        label = item.split(".", 1)[0].strip("* ")
        _, sep, fix = item.partition("fix:")
        if not sep:
            problems.append(f"checklist item {label!r} has no 'Fix:' corrective section")
            continue
        targets = [m.group("target") for m in _MD_LINK.finditer(fix)]
        if not any(t.startswith("#") and t[1:] in anchors for t in targets):
            problems.append(f"checklist item {label!r} has no 'Fix:' link to a guide section")
    return problems


def _is_external(target: str) -> bool:
    scheme = urlsplit(target).scheme
    return bool(scheme) or target.startswith("mailto:") or target.startswith("//")


def find_broken_links(text: str, base_dir: Path, repo_root: Path) -> list[LinkFinding]:
    """Return the Markdown links in ``text`` whose target does not resolve.

    Pure given the filesystem state: takes the document text, the directory the
    document lives in (for relative targets) and the repository root (for
    targets starting with ``/``). External links (a URL scheme, ``mailto:`` or
    a protocol-relative ``//`` target), in-page anchors (``#...``) and anchors
    appended to a resolvable path are not reported. Findings are sorted by line.
    """
    findings: list[LinkFinding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _MD_LINK.finditer(line):
            target = match.group("target")
            if not target or target.startswith("#") or _is_external(target):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            if path_part.startswith("/"):
                resolved = repo_root / path_part.lstrip("/")
            else:
                resolved = base_dir / path_part
            if not resolved.exists():
                findings.append(LinkFinding(number, target))
    return sorted(findings)


def extract_french_section(readme_text: str) -> str | None:
    """Return the body text of the README's French section, or ``None``.

    Pure: looks for a heading whose text contains "français" or "french"
    (case- and accent-insensitive), and returns the text of every line up to
    the next heading of the same or shallower level.
    """
    lines = readme_text.splitlines()
    start = None
    start_level = 0
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if not match:
            continue
        normalized = match.group(2).strip().lower().replace("ç", "c").replace("é", "e")
        if "francais" in normalized or "french" in normalized:
            start = index + 1
            start_level = len(match.group(1))
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        match = _HEADING.match(line)
        if match and len(match.group(1)) <= start_level:
            break
        body.append(line)
    return "\n".join(body)


def french_section_word_count(readme_text: str) -> int | None:
    """Word count of the README's French section, or ``None`` if absent.

    Pure: words are maximal runs of non-whitespace characters, Markdown link
    and heading syntax included, matching a plain word-count reading of the
    rendered prose.
    """
    section = extract_french_section(readme_text)
    if section is None:
        return None
    return len(section.split())


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    args = parser.parse_args(argv)
    repo: Path = args.repo

    deployment_guide = repo / DEPLOYMENT_GUIDE
    readme = repo / README

    problems: list[str] = []

    for rel_path, abs_path in ((DEPLOYMENT_GUIDE, deployment_guide), (README, readme)):
        if not abs_path.is_file():
            print(f"docs-check: ERROR - {rel_path} does not exist", file=sys.stderr)
            return 2
        try:
            _read(abs_path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"docs-check: ERROR - cannot read {rel_path}: {exc}", file=sys.stderr)
            return 2

    deployment_text = _read(deployment_guide)
    readme_text = _read(readme)

    missing = find_missing_checklist_items(deployment_text)
    if missing:
        problems.append(
            f"{DEPLOYMENT_GUIDE}: missing Go_Live_Checklist item(s) for: " + ", ".join(missing)
        )

    for gap in find_go_live_blocking_gaps(deployment_text):
        problems.append(f"{DEPLOYMENT_GUIDE}: {gap}")

    for rel_path, abs_path, text in (
        (DEPLOYMENT_GUIDE, deployment_guide, deployment_text),
        (README, readme, readme_text),
    ):
        for finding in find_broken_links(text, abs_path.parent, repo):
            problems.append(f"{rel_path}: {finding.render()}")

    word_count = french_section_word_count(readme_text)
    if word_count is None:
        problems.append(f"{README}: no French section found")
    elif word_count > FRENCH_SECTION_MAX_WORDS:
        problems.append(
            f"{README}: French section has {word_count} words, "
            f"exceeds the {FRENCH_SECTION_MAX_WORDS}-word limit"
        )

    if problems:
        print(f"docs-check: FAIL - {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("docs-check: OK - checklist complete, all links resolve, French section within limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
