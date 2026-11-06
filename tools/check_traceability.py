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
    "check_spec",
    "claimed_ids",
    "compute_traceability_gaps",
    "main",
    "milestone_ids",
    "parse_requirements",
    "tagged_claims",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = REPO_ROOT / ".kiro" / "specs" / "ai-content" / "requirements.md"
K8S_REQUIREMENTS = REPO_ROOT / ".kiro" / "specs" / "kubernetes-deployment" / "requirements.md"
K8S_REQUIREMENT_NUMBERS = tuple(range(1, 26))
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


_CLAIMS_TAG_RE = re.compile(r"^\s*(?:#|//|\*|/\*)\s*Claims:\s*(?P<ids>[^\n]*)", re.IGNORECASE)


def tagged_claims(root: Path) -> dict[str, list[Path]]:
    """Map each id named in a ``# Claims: <ids>`` tag (``//`` in TypeScript) to its files."""
    claims: dict[str, list[Path]] = {}
    for path in _iter_test_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line in text.splitlines():
            match = _CLAIMS_TAG_RE.match(line)
            if not match:
                continue
            for req_id in _ID_RE.findall(match.group("ids")):
                claims.setdefault(req_id, []).append(path)
    return claims


def compute_traceability_gaps(
    criterion_ids: Iterable[str], claims: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Return ``(unclaimed, unknown)`` ids, each sorted numerically.

    ``unclaimed`` is the set difference criteria minus claims; ``unknown`` is the reverse
    difference, claimed ids that are not criteria.
    """
    criteria = set(criterion_ids)
    claimed = set(claims)
    return (
        sorted(criteria - claimed, key=_id_key),
        sorted(claimed - criteria, key=_id_key),
    )


def check_spec(
    *,
    requirements_path: Path | None = None,
    exceptions_path: Path | None = None,
    tests_root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Gaps of the kubernetes-deployment spec for Requirements 1 to 25.

    Claims come from ``Validates:`` lines and ``Claims:`` tags in test files and from the
    exceptions file. Only ``Claims:`` tags and exceptions can yield unknown ids, because
    ``Validates:`` lines may belong to other specs that reuse the same numbers.
    """
    requirements = parse_requirements(
        (requirements_path or K8S_REQUIREMENTS).read_text(encoding="utf-8")
    )
    criteria: list[str] = []
    for number in K8S_REQUIREMENT_NUMBERS:
        if not requirements.get(number):
            raise ValueError(f"Requirement {number} has no criteria in the requirements file")
        criteria.extend(requirements[number])
    root = tests_root or REPO_ROOT
    tagged = set(tagged_claims(root))
    # The exceptions file belongs to the ai-content spec, whose ids collide numerically with
    # this spec's; it must neither excuse a criterion here nor make an id "unknown".
    validates = set(claimed_ids(root)) & set(criteria)
    unclaimed, _ = compute_traceability_gaps(criteria, tagged | validates)
    _, unknown = compute_traceability_gaps(criteria, tagged)
    return unclaimed, unknown


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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--milestone", choices=sorted(MILESTONES))
    group.add_argument(
        "--spec",
        choices=["kubernetes-deployment"],
        help="check Requirements 1 to 25 of the kubernetes-deployment spec",
    )
    args = parser.parse_args(argv)
    if args.spec:
        return _main_spec()
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


def _main_spec() -> int:
    try:
        unclaimed, unknown = check_spec()
    except (ValueError, OSError, tomllib.TOMLDecodeError) as error:
        print(f"check_traceability: {error}", file=sys.stderr)
        return 2
    if unclaimed or unknown:
        print("Traceability FAILED for kubernetes-deployment:", file=sys.stderr)
        for req_id in unclaimed:
            print(f"  Unclaimed: {req_id}", file=sys.stderr)
        for req_id in unknown:
            print(f"  Unknown: {req_id}", file=sys.stderr)
        return 1
    print("Traceability OK for kubernetes-deployment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
