"""Property-based test for steering fileMatch coverage (task 25.1).

Feature: foundations, Property 37: Steering fileMatch patterns cover their intended real paths

Property 37 (design.md): *For any* Phase-0 steering file intended to cover a
path introduced in this phase, every such real path matches the file's
``fileMatchPattern``, and after any pattern update the file's front matter still
parses as YAML with its inclusion keys intact.

This drives the framework-free coverage checker
(:mod:`tools.steering_coverage`) directly against the live steering files on
disk — the same files Requirement 22 asks the phase to verify and correct — so
the property holds for whichever (steering file, intended real path) pair
Hypothesis draws, with no running Kiro and no simulated inclusion engine. Each
drawn pair names a conditionally-included Phase-0 steering file
(``inclusion: fileMatch``) and one real repository path introduced this phase
that the file is intended to cover (``COVERAGE_SPEC``).

The property asserts, across every drawn pair:

1. **Front matter stays parseable with its inclusion keys intact** (Req 22.3,
   22.4): the file's leading ``---`` block parses and still declares
   ``inclusion: fileMatch`` and a ``fileMatchPattern`` — the guarantee every
   update must preserve.
2. **The intended real path exists** on disk, so the spec cannot drift from the
   repository.
3. **The pattern covers the intended path** (Req 22.1, 22.2, 22.4): the file's
   live ``fileMatchPattern`` matches the path, i.e. editing that path would pull
   the steering file in.

Validates: Requirements 22.1, 22.2, 22.3, 22.4

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from hypothesis import given, settings
from hypothesis import strategies as st

_TOOLS_DIR = Path(__file__).resolve().parents[1]


def _load_steering_coverage() -> ModuleType:
    """Load ``tools/steering_coverage.py`` by file path.

    ``tools/`` is not a package on the pytest import path (the sibling tooling
    tests load their scripts the same way), so the module is imported from its
    file location and registered in ``sys.modules`` before execution.
    """
    module_name = "edu_it_steering_coverage"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _TOOLS_DIR / "steering_coverage.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_coverage = _load_steering_coverage()

# Flatten the coverage spec into (steering file, intended real path) pairs so a
# single sampled strategy exercises every file-and-path the phase must cover.
_COVERAGE_PAIRS: list[tuple[str, str]] = [
    (steering_file, path)
    for steering_file, paths in _coverage.COVERAGE_SPEC.items()
    for path in paths
]


@given(pair=st.sampled_from(_COVERAGE_PAIRS))
@settings(max_examples=200)
def test_property_37_steering_filematch_patterns_cover_their_intended_real_paths(
    pair: tuple[str, str],
) -> None:
    """Feature: foundations, Property 37: Steering fileMatch patterns cover their intended real paths.

    For a conditionally-included Phase-0 steering file and a real path it is
    intended to cover, the file's front matter parses with its inclusion keys
    intact, the path exists on disk, and the live ``fileMatchPattern`` matches
    the path.
    """
    steering_file, intended_path = pair

    # 1. Front matter still parses and keeps its inclusion keys (Req 22.3, 22.4).
    text = _coverage.steering_path(steering_file).read_text(encoding="utf-8")
    front_matter = _coverage.read_front_matter(text)
    assert front_matter.inclusion == "fileMatch", (
        f"{steering_file} must declare inclusion: fileMatch; got {front_matter.inclusion!r}"
    )
    assert front_matter.file_match_pattern, (
        f"{steering_file} must carry a fileMatchPattern in its front matter"
    )

    # 2. The intended real path exists on disk (spec cannot drift from the repo).
    assert (_coverage.REPO_ROOT / intended_path).exists(), (
        f"intended coverage path does not exist: {intended_path}"
    )

    # 3. The pattern covers the intended real path (Req 22.1, 22.2, 22.4).
    assert _coverage.pattern_matches(front_matter.file_match_pattern, intended_path), (
        f"{steering_file} fileMatchPattern does not match intended path {intended_path}: "
        f"{front_matter.file_match_pattern}"
    )
