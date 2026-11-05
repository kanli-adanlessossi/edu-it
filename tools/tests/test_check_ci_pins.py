"""Property test for the CI action pinning check (tools/check_ci_pins.py).

Feature: kubernetes-deployment, Property 26: Workflow actions are pinned by SHA

**Validates: Requirements 15.11**
"""

# Claims: 15.11

from __future__ import annotations

from check_ci_pins import find_unpinned_actions
from hypothesis import given, settings
from hypothesis import strategies as st

_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=10)
_HEX = "0123456789abcdefABCDEF"
_SHA = st.text(alphabet=_HEX, min_size=40, max_size=40)
_REPO = st.builds(lambda o, r: f"{o}/{r}", _NAME, _NAME)

# (kind, reference) where kind is "ok" (not reported) or "bad" (reported).
_PINNED = st.builds(lambda r, s: ("ok", f"{r}@{s}"), _REPO, _SHA)
_LOCAL = st.builds(lambda p: ("ok", f"./{p}"), _NAME)
_TAG = st.builds(
    lambda r, t: ("bad", f"{r}@{t}"),
    _REPO,
    st.one_of(
        st.builds(lambda v: f"v{v}", st.integers(0, 99)),
        st.builds(lambda a, b: f"v{a}.{b}.0", st.integers(0, 9), st.integers(0, 9)),
        st.sampled_from(["main", "master", "latest", "release-1"]),
    ),
)
_BAD_SHA = st.builds(
    lambda r, s: ("bad", f"{r}@{s}"),
    _REPO,
    st.integers(1, 64).filter(lambda n: n != 40).flatmap(lambda n: st.text(alphabet=_HEX, min_size=n, max_size=n)),
)
_NO_REF = st.builds(lambda r: ("bad", r), _REPO)
_DOCKER = st.builds(lambda n: ("bad", f"docker://{n}:latest"), _NAME)
_ENTRY = st.one_of(_PINNED, _LOCAL, _TAG, _BAD_SHA, _NO_REF, _DOCKER)


@st.composite
def _workflows(draw: st.DrawFn) -> tuple[str, list[tuple[int, str]]]:
    """Build workflow text plus the expected (line, reference) findings."""
    lines: list[str] = ["name: x", "jobs:", "  a:", "    steps:"]
    expected: list[tuple[int, str]] = []
    for kind, ref in draw(st.lists(_ENTRY, max_size=12)):
        dash = draw(st.booleans())
        quote = draw(st.sampled_from(["", "'", '"']))
        comment = draw(st.sampled_from(["", "  # v4.1.0"]))
        prefix = "      - " if dash else "        "
        lines.append(f"{prefix}uses: {quote}{ref}{quote}{comment}")
        if kind == "bad":
            expected.append((len(lines), ref))
        # Noise that must never be reported.
        if draw(st.booleans()):
            lines.append(f"      # uses: {draw(_REPO)}@v1")
        if draw(st.booleans()):
            lines.append("      - run: echo hi")
    return "\n".join(lines) + "\n", expected


# Claims: 15.11
@settings(max_examples=200, deadline=None)
@given(_workflows())
def test_reports_exactly_the_unpinned_third_party_references(case: tuple[str, list[tuple[int, str]]]) -> None:
    text, expected = case
    found = [(f.line, f.reference) for f in find_unpinned_actions(text)]
    assert found == expected


# Claims: 15.11
@settings(max_examples=100, deadline=None)
@given(_workflows())
def test_findings_are_sorted_and_deterministic(case: tuple[str, list[tuple[int, str]]]) -> None:
    text, _ = case
    first = find_unpinned_actions(text)
    assert first == sorted(first)
    assert first == find_unpinned_actions(text)
