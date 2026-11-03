#!/usr/bin/env python3
"""Incremental requirement-to-test traceability check (ai-content, Req 23.2, 23.5).

At the end of each milestone the project team checks that every requirement of
that milestone is claimed by at least one automated test. A test claims a
requirement through a ``Validates: Requirements 1.2, 1.3`` line in a docstring
(Python modules, classes and functions) or in a comment/docstring of a
``*.test.ts`` / ``*.test.tsx`` file.

Requirement ids are acceptance criteria written ``N.M`` and are read from the
spec's ``requirements.md`` (``### Requirement N`` sections with numbered
criteria). A requirement that no test claims must either get a test or be
listed with a reason in the exceptions file ``tools/traceability_exceptions.toml``
(requirements verified without a test, e.g. owner actions). The owner reviews
that file.

Milestone sets::

    A   = Requirements 1 to 12, plus 22.1 and 22.4
    B   = Requirements 13 to 16, plus 22.2 and 22.5 to 22.7
    C   = Requirements 17 to 21, plus 22.3
    all = A, B and C, plus Requirements 23 to 25

Usage::

    python tools/check_traceability.py --milestone {A,B,C,all}

Exits 0 when every requirement of the milestone is claimed or excepted, and
non-zero naming each requirement that is not. Stdlib only.

Note: a ``Validates:`` line carries no spec name, so a claim of an id also used
by another spec counts here. Keep ids of this spec in the tests of this spec.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

__all__ = [
    "MILESTONES",
    "check",
    "claimed_ids",
    "main",
    "milestone_ids",
    "parse_requirements",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = REPO_ROOT / ".kiro" / "specs" / "ai-content" / "requirements.md"
DEFAULT_EXCEPTIONS = Path(__file__).resolve().parent / "traceability_exceptions.toml"

# Milestone -> (whole requirement numbers, individual criterion ids).
_A = (tuple(range(1, 13)), ("22.1", "22.4"))
_B = (tuple(range(13, 17)), ("22.2", "22.5", "22.6", "22.7"))
_C = (tuple(range(17, 22)), ("22.3",))
MILESTONES: dict[str, tuple[tuple[int, ...], tuple[str, ...]]] = {
    "A": _A,
    "B": _B,
    "C": _C,
    "all": (_A[0] + _B[0] + _C[0] + (23, 24, 25), _A[1] + _B[1] + _C[1]),
}

_SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".hypothesis", ".pytest_cache", ".next"}
)
_REQ_HEADING_RE = re.compile(r"^###\s+Requirement\s+(\d+)\b")
_CRITERION_RE = re.compile(r"^(\d+)\.\s+\S")
_VALIDATES_RE = re.compile(r"Validates:\s*\**\s*Requirements?\s+(?P<ids>[^\n]*)", re.IGNORECASE)
_ID_RE = re.compile(r"\b\d+\.\d+\b")
# A continuation line of a claim that only holds ids, commas and an "and".
_CONTINUATION_RE = re.compile(r"^\s*(?:[#*/]+\s*)?[\d.,\s*]*(?:and\s+[\d.,\s*]*)?$")


def parse_requirements(text: str) -> dict[int, list[str]]:
    """Map each requirement number to its criterion ids (``["1.1", "1.2", ...]``)."""
    result: dict[int, list[str]] = {}
    current: int | None = None
    for line in text.splitlines():
        heading = _REQ_HEADING_RE.match(line)
        if heading:
            current = int(heading.group(1))
            result[current] = []
            continue
        if re.match(r"#{1,3}\s", line):
            # Any other section heading (e.g. a milestone) closes the current requirement.
            current = None
            continue
        if current is None:
            continue
        criterion = _CRITERION_RE.match(line)
        if criterion:
            result[current].append(f"{current}.{criterion.group(1)}")
    return result


def milestone_ids(milestone: str, requirements: Mapping[int, list[str]]) -> list[str]:
    """Ordered requirement ids of ``milestone``.

    Raises :class:`ValueError` for an unknown milestone or an id the spec lacks.
    """
    if milestone not in MILESTONES:
        raise ValueError(f"unknown milestone {milestone!r}; expected one of {sorted(MILESTONES)}")
    numbers, extra = MILESTONES[milestone]
    ids: list[str] = []
    for number in numbers:
        if number not in requirements or not requirements[number]:
            raise ValueError(f"Requirement {number} has no criteria in the requirements file")
        ids.extend(requirements[number])
    known = {i for criteria in requirements.values() for i in criteria}
    for extra_id in extra:
        if extra_id not in known:
            raise ValueError(f"Requirement {extra_id} is not in the requirements file")
        ids.append(extra_id)
    return sorted(set(ids), key=_id_key)


def _id_key(req_id: str) -> tuple[int, int]:
    major, minor = req_id.split(".")
    return int(major), int(minor)


def _ids_from_text(text: str) -> set[str]:
    """Extract ids from every ``Validates: Requirements ...`` claim in ``text``."""
    found: set[str] = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = _VALIDATES_RE.search(line)
        if not match:
            continue
        found.update(_ID_RE.findall(match.group("ids")))
        for follow in lines[index + 1 :]:
            if not follow.strip() or not _CONTINUATION_RE.match(follow):
                break
            found.update(_ID_RE.findall(follow))
    return found


def _python_claims(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            doc = ast.get_docstring(node, clean=True)
            if doc:
                found |= _ids_from_text(doc)
    return found


def _iter_test_files(root: Path) -> Iterable[Path]:
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
            elif (
                entry.suffix == ".py"
                and (entry.name.startswith("test_") or entry.name.endswith("_test.py"))
            ) or entry.name.endswith((".test.ts", ".test.tsx")):
                yield entry


def claimed_ids(root: Path) -> dict[str, list[Path]]:
    """Map each claimed requirement id to the test files that claim it."""
    claims: dict[str, list[Path]] = {}
    for path in _iter_test_files(root):
        if path.suffix == ".py":
            ids = _python_claims(path)
        else:
            try:
                ids = _ids_from_text(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, OSError):
                continue
        for req_id in ids:
            claims.setdefault(req_id, []).append(path)
    return claims


def load_exceptions(path: Path) -> dict[str, str]:
    """Read ``[[exception]]`` entries (``id`` and non-empty ``reason``)."""
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    exceptions: dict[str, str] = {}
    for entry in data.get("exception", []):
        req_id = str(entry.get("id", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if not _ID_RE.fullmatch(req_id):
            raise ValueError(f"exceptions file: invalid requirement id {req_id!r}")
        if not reason:
            raise ValueError(f"exceptions file: requirement {req_id} has no reason")
        exceptions[req_id] = reason
    return exceptions


def check(
    milestone: str,
    *,
    requirements_path: Path | None = None,
    exceptions_path: Path | None = None,
    tests_root: Path | None = None,
) -> list[str]:
    """Return the milestone's requirement ids that no test claims and no exception covers."""
    requirements = parse_requirements(
        (requirements_path or DEFAULT_REQUIREMENTS).read_text(encoding="utf-8")
    )
    ids = milestone_ids(milestone, requirements)
    claimed = claimed_ids(tests_root or REPO_ROOT)
    excepted = load_exceptions(exceptions_path or DEFAULT_EXCEPTIONS)
    return [i for i in ids if i not in claimed and i not in excepted]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--milestone", required=True, choices=sorted(MILESTONES))
    args = parser.parse_args(argv)
    try:
        missing = check(args.milestone)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as error:
        print(f"check_traceability: {error}", file=sys.stderr)
        return 2
    if missing:
        print(
            f"Traceability FAILED for milestone {args.milestone}: "
            f"{len(missing)} requirement(s) have no test claiming them:",
            file=sys.stderr,
        )
        for req_id in missing:
            print(f"  Requirement {req_id}", file=sys.stderr)
        print(
            "Add a test with a 'Validates: Requirements ...' docstring line, or list the "
            "requirement with a reason in tools/traceability_exceptions.toml.",
            file=sys.stderr,
        )
        return 1
    print(f"Traceability OK for milestone {args.milestone}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
