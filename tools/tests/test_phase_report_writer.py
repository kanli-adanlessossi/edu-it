"""Phase-3 report writer (hands-on-labs task 22.3; Requirements 31.4, 31.5, 31.8, 35.1-35.4).

The writer must record the command and real outcome of each check, the final performance
attempt's measurements, the Requirement 33 mapping with group outcomes and the Requirement 23
review outcome, and must refuse (writing nothing) when any check failed or did not run or the
final performance attempt missed a target.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import phase_report as pr  # noqa: E402
import run_verification as rv  # noqa: E402


def _target(values: list[float], limit: float) -> dict:
    return {"method": "driver", "values_seconds": values, "limit_seconds": limit}


def _attempt(number: int, *, start: float = 1.0, key: float = 0.01, check: float = 1.0) -> dict:
    return {
        "attempt": number,
        "targets": {
            "start_to_ready": _target([start] * 33, 20.0),
            "keystroke_round_trip": _target([key] * 203, 0.15),
            "shell_check": _target([check] * 4, 15.0),
        },
    }


def _perf(*attempts: dict) -> dict:
    return {"machine": {"cpu": "test"}, "attempts": list(attempts)}


def _checks(status: str = "passed") -> list[pr.CheckRecord]:
    return [pr.CheckRecord("labs-unit", "5", "unit", "python -m pytest -q", status, "3 passed")]


RULE_MAP = [("Rule A", ["7.4"], [("tools/tests/x.py", "needle")])]


def _groups(status: str = "passed") -> dict[str, pr.GroupRecord]:
    groups = pr.group_mapping(RULE_MAP)
    for group in groups.values():
        group.status = status
    return groups


def _review(**overrides) -> pr.ReviewOutcome:
    values = dict(
        labs={"lab.a": {"reviewed_by": "someone", "reviewed_at": "2026-11-02T09:00:00Z"}},
        unreviewed=[],
        pending_ids=[],
        strict_validation="passed",
        approval_flow="passed",
    )
    values.update(overrides)
    return pr.ReviewOutcome(**values)


def _write(tmp_path: Path, **overrides) -> Path:
    args = dict(
        path=tmp_path / "phase-3.md",
        checks=_checks(),
        perf=_perf(_attempt(1)),
        rule_map=RULE_MAP,
        groups=_groups(),
        review=_review(),
        required_check_ids=["labs-unit"],
    )
    args.update(overrides)
    return pr.write_phase_report(**args)


def test_writes_report_with_commands_measurements_mapping_and_review(tmp_path: Path) -> None:
    text = _write(tmp_path).read_text(encoding="utf-8")
    assert "python -m pytest -q" in text and "PASSED: 3 passed" in text
    assert "nearest-rank" in text and "Sample count: 30 counted (33 raw" in text
    assert '"cpu": "test"' in text
    assert "Rule A" in text and "`tools/tests/x.py`" in text
    assert "reviewer recorded: `someone`" in text and "Pending_Review_List: empty" in text


@pytest.mark.parametrize("status", ["failed", "not-run"])
def test_refuses_when_a_check_failed_or_did_not_run(tmp_path: Path, status: str) -> None:
    with pytest.raises(pr.PhaseReportRefused):
        _write(tmp_path, checks=_checks(status))
    assert not (tmp_path / "phase-3.md").exists()


def _multi_checks(second_status: str = "passed") -> list[pr.CheckRecord]:
    return [
        pr.CheckRecord("labs-unit", "5", "unit", "python -m pytest -q", "passed", "3 passed"),
        pr.CheckRecord("labs-web", "6", "web", "npm run test", second_status, "boom"),
        pr.CheckRecord("labs-axe", "7", "axe", "npx playwright test", "passed", ""),
    ]


def test_report_lists_command_and_pass_outcome_for_every_check(tmp_path: Path) -> None:
    # Requirements 35.1, 35.3: when all pass, each check's command and outcome is listed.
    checks = _multi_checks()
    text = _write(tmp_path, checks=checks, required_check_ids=[c.check_id for c in checks])
    body = text.read_text(encoding="utf-8")
    for check in checks:
        row = next(line for line in body.splitlines() if f"`{check.check_id}`" in line)
        assert f"`{check.command}`" in row and "PASSED" in row


@pytest.mark.parametrize("status", ["failed", "not-run"])
def test_one_bad_check_among_passing_ones_writes_nothing_and_creates_no_directory(
    tmp_path: Path, status: str
) -> None:
    target = tmp_path / "nested" / "reports" / "phase-3.md"
    checks = _multi_checks(status)
    with pytest.raises(pr.PhaseReportRefused) as refusal:
        _write(tmp_path, path=target, checks=checks)
    assert any("labs-web" in b for b in refusal.value.blockers)
    assert not target.exists() and not target.parent.exists()


def test_refusal_does_not_overwrite_an_existing_report(tmp_path: Path) -> None:
    target = tmp_path / "phase-3.md"
    target.write_text("previous", encoding="utf-8")
    with pytest.raises(pr.PhaseReportRefused):
        _write(tmp_path, path=target, checks=_multi_checks("failed"))
    assert target.read_text(encoding="utf-8") == "previous"


def test_refuses_when_a_required_check_is_absent(tmp_path: Path) -> None:
    with pytest.raises(pr.PhaseReportRefused, match="labs-performance was not run"):
        _write(tmp_path, required_check_ids=["labs-unit", "labs-performance"])
    assert not (tmp_path / "phase-3.md").exists()


def test_refuses_when_final_attempt_misses_a_target_even_if_the_first_passed(
    tmp_path: Path,
) -> None:
    # A log that passed, then "re-measured" and missed, must be refused: only the final counts.
    perf = _perf(_attempt(1), _attempt(2, key=0.2))
    with pytest.raises(pr.PhaseReportRefused) as refusal:
        _write(tmp_path, perf=perf)
    assert any("keystroke_round_trip" in b for b in refusal.value.blockers)
    assert not (tmp_path / "phase-3.md").exists()


def test_failed_first_attempt_then_passing_final_attempt_writes_and_logs_both(
    tmp_path: Path,
) -> None:
    perf = _perf(_attempt(1, start=25.0), _attempt(2))
    text = _write(tmp_path, perf=perf).read_text(encoding="utf-8")
    assert "| 1 | start_to_ready |" in text and "| 2 | start_to_ready |" in text
    assert "NO |" in text  # the failed attempt is recorded
    lines = pr.format_perf_attempts(perf)
    assert any("attempt 1 start_to_ready" in line and "met=False" in line for line in lines)
    assert any("final attempt" in line and line.endswith("2") for line in lines)


def test_verdict_is_recomputed_from_samples_not_the_stored_flag(tmp_path: Path) -> None:
    perf = _perf(_attempt(1, key=0.5))
    perf["attempts"][0]["targets"]["keystroke_round_trip"]["met"] = True
    with pytest.raises(pr.PhaseReportRefused):
        _write(tmp_path, perf=perf)


def test_too_few_counted_samples_refuse(tmp_path: Path) -> None:
    perf = _perf(_attempt(1))
    perf["attempts"][0]["targets"]["start_to_ready"]["values_seconds"] = [1.0] * 32
    with pytest.raises(pr.PhaseReportRefused, match="below the minimum"):
        _write(tmp_path, perf=perf)


def test_more_than_one_re_measurement_refuses(tmp_path: Path) -> None:
    perf = _perf(_attempt(1, start=30.0), _attempt(2, start=30.0), _attempt(3))
    with pytest.raises(pr.PhaseReportRefused, match="at most one re-measurement"):
        _write(tmp_path, perf=perf)


def test_missing_perf_log_machine_or_mapping_or_group_failure_refuse(tmp_path: Path) -> None:
    with pytest.raises(pr.PhaseReportRefused, match="missing or unreadable"):
        _write(tmp_path, perf=None)
    no_machine = copy.deepcopy(_perf(_attempt(1)))
    no_machine["machine"] = {}
    with pytest.raises(pr.PhaseReportRefused, match="machine setup"):
        _write(tmp_path, perf=no_machine)
    with pytest.raises(pr.PhaseReportRefused, match="mapping unavailable"):
        _write(tmp_path, groups={}, mapping_error="FileNotFoundError: suite")
    with pytest.raises(pr.PhaseReportRefused, match="test group"):
        _write(tmp_path, groups=_groups("failed"))
    assert not (tmp_path / "phase-3.md").exists()


@pytest.mark.parametrize(
    "override",
    [
        {"unreviewed": ["lab.b"]},
        {"pending_ids": ["lab.b"]},
        {"strict_validation": "failed"},
        {"approval_flow": "not-run"},
    ],
)
def test_review_outcome_must_be_clean(tmp_path: Path, override: dict) -> None:
    with pytest.raises(pr.PhaseReportRefused, match="Requirement 23"):
        _write(tmp_path, review=_review(**override))


def test_load_perf_log_accepts_embedded_harness_output(tmp_path: Path) -> None:
    import json

    path = tmp_path / "perf.json"
    path.write_text(json.dumps({"harness_output": _perf(_attempt(1))}), encoding="utf-8")
    assert pr.perf_blockers(pr.load_perf_log(path)) == []
    assert pr.load_perf_log(tmp_path / "absent.json") is None


def test_committed_perf_results_attempt_log_is_valid() -> None:
    perf = pr.load_perf_log(TOOLS / "perf" / "perf-results.json")
    assert pr.perf_blockers(perf) == []


def test_runner_wires_every_required_labs_command_and_marks_missing_tools_not_run() -> None:
    ids = {c.check_id for c in rv.build_labs_checks()}
    for needed in (
        "labs-unit",
        "labs-property",
        "labs-docker",
        "labs-web",
        "labs-playwright",
        "labs-axe",
        "labs-content-strict",
        "labs-content-pending",
        "labs-adr-pins",
        "labs-traceability",
        "labs-performance",
        "labs-image-build",
        "labs-image-scan-lab-linux-base",
        "labs-image-scan-lab-python",
    ):
        assert needed in ids
    missing = rv.Check("x", "1", "d", ["definitely-not-an-installed-tool-xyz"])
    rv.run_check(missing)
    assert missing.status == "not-run"
    skipped = [c for c in rv.build_labs_checks(skip_perf=True) if c.check_id == "labs-performance"]
    assert skipped[0].command is None
