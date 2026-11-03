"""Phase-4 report guard property (ai-content task 26.3).

Feature: ai-content, Property 39: The phase report is passing only when every check ran and passed

For any set of acceptance check results, the writer marks the Phase_4_Report passing if and only if
every required check is present and passed. Otherwise it is marked NOT PASSING and names every check
that did not run or did not pass.

Validates: Requirements 25.6
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import phase_report as pr  # noqa: E402

_IDS = [f"check-{i}" for i in range(8)]


def _inputs(complete: bool) -> pr.Phase4Inputs:
    return pr.Phase4Inputs(
        live_run_cost_usd="1.2345" if complete else None,
        cost_cap_usd="5.00",
        models=["model-a"],
        branches=["generation/run-1"],
        eval_report={"suite": "s", "passed": 1, "total": 1, "pass_rate": 1.0, "outcomes": []},
        not_verified=[],
    )


@st.composite
def _scenarios(draw):
    ran = draw(st.lists(st.sampled_from(_IDS), unique=True, max_size=8))
    statuses = [draw(st.sampled_from(["passed", "failed", "not-run"])) for _ in ran]
    checks = [
        pr.CheckRecord(cid, "25", "desc", f"cmd {cid}", status, "detail")
        for cid, status in zip(ran, statuses, strict=True)
    ]
    required = draw(st.lists(st.sampled_from(_IDS), unique=True, max_size=8))
    return checks, required, draw(st.booleans())


@settings(max_examples=100, deadline=None)
@given(_scenarios())
def test_report_is_passing_only_when_every_check_ran_and_passed(scenario) -> None:
    """Validates: Requirements 25.6"""
    checks, required, complete = scenario
    by_id = {c.check_id: c for c in checks}
    expected_bad = {c.check_id for c in checks if c.status != "passed"} | {
        r for r in required if r not in by_id
    }
    expect_pass = not expected_bad and complete

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "phase-4.md"
        result = pr.write_phase4_report(out, checks, _inputs(complete), required_check_ids=required)
        text = out.read_text(encoding="utf-8")

    assert result.passing is expect_pass
    assert (pr.PASSING_MARK in text) is expect_pass
    assert (pr.NOT_PASSING_MARK in text) is (not expect_pass)
    joined = "\n".join(result.blockers)
    for check_id in expected_bad:
        assert check_id in joined
        assert check_id in text
