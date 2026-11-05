"""Property 31: Report status (kubernetes-deployment task 6.20).

For any list of required checks with outcomes passed, failed or not run, the report status is
passing if and only if every check passed, and every check that was not run appears in the
Not verified section.

**Validates: Requirements 26.4, 26.5, 26.6**
"""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import phase_report as pr  # noqa: E402

_outcomes = st.sampled_from([pr.PASSED, pr.FAILED, pr.NOT_RUN])


@st.composite
def _checks(draw: st.DrawFn) -> list[pr.Phase5Check]:
    outcomes = draw(st.lists(_outcomes, max_size=12))
    return [
        pr.Phase5Check(
            milestone=draw(st.sampled_from(pr.MILESTONES)),
            check_id=f"check-{i}",
            command=f"cmd {i}",
            outcome=outcome,
            reason=draw(st.text(alphabet="abcdef ", max_size=10)) if outcome != pr.PASSED else "",
        )
        for i, outcome in enumerate(outcomes)
    ]


@settings(max_examples=100, deadline=None)
@given(checks=_checks())
def test_property_31_report_status(checks: list[pr.Phase5Check]) -> None:
    """Passing iff all passed; every not-run check is listed under Not verified."""
    status = pr.report_status(checks)
    all_passed = all(c.outcome == pr.PASSED for c in checks)
    assert status == (pr.PHASE5_PASSING if all_passed else pr.PHASE5_NOT_PASSING)

    listed = {name for name, _ in pr.not_verified_entries(checks)}
    for check in checks:
        if check.outcome == pr.NOT_RUN:
            assert check.check_id in listed
        else:
            assert check.check_id not in listed

    rendered = pr.render_phase5_report(checks, pr.Phase5Inputs())
    section = rendered.split("## 3. Not verified", 1)[1].split("## 4.", 1)[0]
    for check in checks:
        if check.outcome == pr.NOT_RUN:
            assert check.check_id in section
    assert f"Status: {status.upper()}" in rendered
