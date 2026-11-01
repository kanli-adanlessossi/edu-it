"""Regression tests for the phase-3 report review section, header and outcome summaries.

Found in the review of the first written docs/reports/phase-3.md: the review section showed
one lab as `?` (the stable id lives under ``provenance``), the header named a single
``--phase-report`` run for a report merged from the two gate phases, and some outcomes showed
a trailing warning line instead of the test-count summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import phase_report as pr  # noqa: E402
import run_verification as rv  # noqa: E402


def _seed(tmp_path: Path, labs: list[dict]) -> Path:
    seed = tmp_path / "content" / "linux-fundamentals"
    seed.mkdir(parents=True)
    (seed / "labs.json").write_text(json.dumps(labs), encoding="utf-8")
    (seed / "pending-review.json").write_text(json.dumps({"stable_ids": []}), encoding="utf-8")
    return tmp_path


def _lab(stable_id: str) -> dict:
    return {
        "provenance": {
            "stable_id": stable_id,
            "reviewed_by": "reviewer",
            "reviewed_at": "2026-11-02T09:00:00Z",
        }
    }


def test_review_outcome_reads_each_lab_stable_id_from_its_provenance(tmp_path: Path) -> None:
    repo = _seed(tmp_path, [_lab("lab.one"), _lab("lab.two")])
    review = pr.read_review_outcome(repo, strict_validation="passed", approval_flow="passed")
    assert sorted(review.labs) == ["lab.one", "lab.two"]
    assert review.unreviewed == []


def test_review_outcome_lists_an_unreviewed_lab_by_its_provenance_id(tmp_path: Path) -> None:
    unreviewed = {"provenance": {"stable_id": "lab.pending"}}
    repo = _seed(tmp_path, [_lab("lab.one"), unreviewed])
    review = pr.read_review_outcome(repo, strict_validation="passed", approval_flow="passed")
    assert review.unreviewed == ["lab.pending"]


def _render(**kwargs) -> str:
    perf = {
        "machine": {},
        "attempts": [
            {
                "attempt": 1,
                "targets": {
                    "start_to_ready": {
                        "method": "d",
                        "values_seconds": [1.0] * 33,
                        "limit_seconds": 20.0,
                    },
                    "keystroke_round_trip": {
                        "method": "d",
                        "values_seconds": [0.01] * 203,
                        "limit_seconds": 0.15,
                    },
                    "shell_check": {
                        "method": "d",
                        "values_seconds": [1.0] * 4,
                        "limit_seconds": 15.0,
                    },
                },
            }
        ],
    }
    review = pr.ReviewOutcome({}, [], [], "passed", "passed")
    checks = [pr.CheckRecord("labs-unit", "5", "unit", "pytest", "passed", "3 passed")]
    return pr.render_phase_report(checks, perf, [], {}, review, **kwargs)


def test_header_defaults_to_the_single_run_wording() -> None:
    assert "by `tools/run_verification.py --suite labs --phase-report`." in _render()


def test_header_names_the_procedure_when_one_is_given() -> None:
    text = _render(procedure="two-phase gate: phase 1 stack down, phase 2 stack up")
    assert "two-phase gate: phase 1 stack down, phase 2 stack up" in text
    assert "--phase-report`." not in text


def test_outcome_summary_prefers_the_pytest_result_line_over_a_trailing_warning() -> None:
    output = "\n".join(
        [
            "...",
            "12 passed, 2 skipped in 30.1s",
            "Exception ignored in: <function X.__del__>",
            "  warnings.warn(pytest.PytestUnraisableExceptionWarning(msg))",
        ]
    )
    assert rv.summarize_output(output) == "12 passed, 2 skipped in 30.1s"


def test_outcome_summary_prefers_the_vitest_and_playwright_totals() -> None:
    vitest = " Test Files  38 passed (38)\n      Tests  225 passed (225)\n   Duration  9s\n  hint"
    assert rv.summarize_output(vitest) == "Tests  225 passed (225)"
    playwright = "  ok 1 a\n  2 passed (12.0s)\n[WebServer]   Learn more: https://nextjs.org"
    assert rv.summarize_output(playwright) == "2 passed (12.0s)"


def test_outcome_summary_falls_back_to_the_last_line() -> None:
    assert rv.summarize_output("content valid: 80 item(s)\n") == "content valid: 80 item(s)"
    assert rv.summarize_output("") == ""
