"""Property test for the world-writable-entries seed program classification.

Feature: hands-on-labs, Property 28: The seed program classifies entries by the stated rule

**Validates: Requirements 22.2, 22.4, 22.5**

The in-sandbox harness needs ``O_NOFOLLOW`` and so cannot run on Windows. This test therefore
checks the stated rule without the harness: a pure reference implementation (parse ``ls -l`` text)
is compared with (a) an oracle computed from structured entries for generated listings and (b) the
authored cases and expected outputs shipped in the lab bundle.

Rule: report entries that are not symlinks and have the world-write bit, in input order, including
world-writable sticky directories (``drwxrwxrwt``) and excluding sticky entries without world-write
(``drwxr-xr-t``); skip ``total`` and blank lines; names may contain spaces.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "content"
    / "linux-fundamentals"
    / "lab-bundles"
    / "world-writable-entries"
)


def reference_solution(listing: str) -> str:
    """Pure reference implementation of the stated rule."""
    out: list[str] = []
    for line in listing.splitlines():
        if not line.strip() or line.startswith("total"):
            continue
        fields = line.split(None, 8)
        if len(fields) < 9:
            continue
        mode, name = fields[0], fields[8]
        if mode[0] == "l" or len(mode) < 10:
            continue
        if mode[8] == "w":
            out.append(name)
    return "".join(n + "\n" for n in out)


# --- generated listings ------------------------------------------------------------------------

_TYPES = st.sampled_from(["-", "d", "l"])
_OTHER_R = st.sampled_from(["r", "-"])
_OTHER_W = st.sampled_from(["w", "-"])
_OTHER_X = st.sampled_from(["x", "-", "t", "T"])
_BITS = st.text(alphabet="rwx-", min_size=6, max_size=6)
_NAME = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), max_codepoint=0x24F),
    min_size=1,
    max_size=6,
)
_NAME_WITH_SPACES = st.lists(_NAME, min_size=1, max_size=3).map(" ".join)


@st.composite
def _entry(draw: st.DrawFn) -> tuple[str, str, bool, str]:
    kind = draw(_TYPES)
    perms = draw(_BITS) + draw(_OTHER_R) + draw(_OTHER_W) + draw(_OTHER_X)
    name = draw(_NAME_WITH_SPACES)
    expected = kind != "l" and perms[7] == "w"
    line = f"{kind}{perms} 1 learner learner 1024 Mar  3 09:15 {name}"
    return kind, perms, expected, (line if kind != "l" else f"{line} -> target")


@settings(max_examples=200, deadline=None)
@given(
    entries=st.lists(_entry(), max_size=12),
    header=st.sampled_from(["", "total 0\n", "total 12\n"]),
)
def test_reference_classifies_generated_listings(
    entries: list[tuple[str, str, bool, str]], header: str
) -> None:
    listing = header + "".join(line + "\n" for *_, line in entries)
    names = []
    for kind, _perms, expected, line in entries:
        if expected:
            names.append(line.split(None, 8)[8])
    assert reference_solution(listing) == "".join(n + "\n" for n in names)


def test_empty_input_prints_nothing() -> None:
    assert reference_solution("") == ""


def test_sticky_rule_examples() -> None:
    listing = (
        "drwxrwxrwt 10 root root 4096 Mar  3 09:15 tmp\n"
        "drwxr-xr-t 2 root root 4096 Mar  3 09:15 shared\n"
        "lrwxrwxrwx 1 root root 4 Mar  3 09:15 link -> tmp\n"
    )
    assert reference_solution(listing) == "tmp\n"


# --- authored cases ----------------------------------------------------------------------------

_CASE_FILES = sorted((BUNDLE / "sandbox" / "cases").glob("*.json"))


def _authored() -> list[tuple[str, str, str]]:
    params = []
    for case_file in _CASE_FILES:
        expected = json.loads((BUNDLE / "expected" / case_file.name).read_text("utf-8"))["cases"]
        for case in json.loads(case_file.read_text("utf-8"))["cases"]:
            cid = case["case_id"]
            stdin = base64.b64decode(case["stdin_b64"]).decode("utf-8")
            out = base64.b64decode(expected[cid]["stdout_b64"]).decode("utf-8")
            params.append((f"{case_file.stem}:{cid}", stdin, out))
    return params


def test_authored_cases_exist() -> None:
    assert len(_CASE_FILES) >= 2
    assert len(_authored()) >= 10


@pytest.mark.parametrize(("label", "stdin", "expected"), _authored())
def test_reference_matches_authored_expected_outputs(label: str, stdin: str, expected: str) -> None:
    assert reference_solution(stdin) == expected, label
