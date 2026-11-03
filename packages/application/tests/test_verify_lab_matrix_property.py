"""Property test for the VerifyLab check matrix (ai-content task 7.6).

Feature: ai-content, Property 18: Lab findings follow the check matrix

For any lab with checks and any pass or fail result of each check on the Starting_State and
on the Reference_Solution, every check is run in both sandboxes, a finding exists for a check
if and only if it passes on the Starting_State or fails on the Reference_Solution, and the lab
is flagged if and only if at least one such finding exists.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4**
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from edu_it_application.ai import VerifyLab
from edu_it_domain.ai.verification import (
    FindingKind,
    LabCheckUnderVerification,
    LabUnderVerification,
)
from edu_it_domain.ports.sandbox import ExecResult
from edu_it_infrastructure.fakes import Fake_SandboxProvider, Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from collections.abc import Sequence

    from edu_it_infrastructure.fakes.sandbox_provider import FakeSandbox

_SOLVE = "SOLVE"
_SOLVED = "/solved"

# One (passes on starting state, passes on reference solution) pair per check.
_matrix = st.lists(st.tuples(st.booleans(), st.booleans()), min_size=1, max_size=8)


@settings(max_examples=150, deadline=None)
@given(matrix=_matrix)
def test_lab_findings_follow_the_check_matrix(matrix: list[tuple[bool, bool]]) -> None:
    """Feature: ai-content, Property 18: Lab findings follow the check matrix."""
    clock = Test_Clock()
    sandbox = Fake_SandboxProvider(clock)
    outcomes = {f"c{i}": pair for i, pair in enumerate(matrix)}
    runs: Counter[tuple[str, bool]] = Counter()

    def handler(sb: FakeSandbox, command: Sequence[str], as_user: str) -> ExecResult | None:
        script = command[2]
        if script == _SOLVE:
            sb.files[_SOLVED] = b"1"
            return ExecResult(0, b"", b"", False)
        if script.startswith("CHECK:"):
            check_id = script.removeprefix("CHECK:")
            reference = _SOLVED in sb.files
            runs[(check_id, reference)] += 1
            starting_ok, reference_ok = outcomes[check_id]
            passed = reference_ok if reference else starting_ok
            return ExecResult(0 if passed else 1, b"", b"", False)
        return None

    sandbox.exec_handler = handler
    lab = LabUnderVerification(
        lab_id="lab1",
        image="img",
        checks=tuple(LabCheckUnderVerification(cid, f"CHECK:{cid}", 5.0) for cid in outcomes),
        reference_solution=_SOLVE,
        setup_script=None,
    )

    findings = VerifyLab(sandbox=sandbox, clock=clock).execute(lab)

    # Every check ran exactly once in each sandbox.
    for cid in outcomes:
        assert runs[(cid, False)] == 1
        assert runs[(cid, True)] == 1

    expected: set[tuple[FindingKind, str]] = set()
    for cid, (starting_ok, reference_ok) in outcomes.items():
        if starting_ok:
            expected.add((FindingKind.CHECK_PASSES_ON_STARTING_STATE, cid))
        if not reference_ok:
            expected.add((FindingKind.CHECK_FAILS_ON_REFERENCE_SOLUTION, cid))

    actual = [(f.kind, f.target) for f in findings]
    assert len(actual) == len(set(actual))
    assert set(actual) == expected
    # The lab is flagged iff at least one finding exists.
    assert bool(findings) == bool(expected)
    assert sandbox.list_sandboxes() == ()
