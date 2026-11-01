"""Performance attempt lines come only from a log written by this run (api-route-wiring 14.5;
Requirements 17.2, 17.3).

``labs-performance`` needs the Real_Stack. When it fails, the attempt log on disk is the one from
an earlier run, and printing it would look like a pass. Process calls are injected.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_verification as rv

_ATTEMPT_LINE = "performance attempt 1 start_to_ready"
_LOG = {
    "attempts": [
        {
            "attempt": 1,
            "targets": {
                "start_to_ready": {"values_seconds": [0.5] * 33, "method": "p95"},
            },
        }
    ],
    "machine": {"os": "test"},
}


@pytest.fixture
def old_log(tmp_path: Path) -> Path:
    path = tmp_path / "perf.json"
    path.write_text(json.dumps(_LOG), encoding="utf-8")
    past = time.time() - 3600
    os.utime(path, (past, past))
    return path


def _run_labs(
    monkeypatch: pytest.MonkeyPatch, capsys, log: Path, only: list[str], status: str
) -> str:
    def fake_run_check(check, **kwargs):
        check.status = status
        check.detail = "fake"

    monkeypatch.setattr(rv, "run_check", fake_run_check)
    argv = ["--suite", "labs", "--perf-log", str(log), "--only", *only]
    rv.main(argv)
    return capsys.readouterr().out


def test_failed_labs_performance_does_not_print_the_stale_attempts(
    monkeypatch, capsys, old_log
) -> None:
    out = _run_labs(monkeypatch, capsys, old_log, ["labs-performance"], "failed")
    assert _ATTEMPT_LINE not in out
    assert "not written by this run" in out


def test_attempts_are_not_printed_when_labs_performance_was_not_run(
    monkeypatch, capsys, old_log
) -> None:
    out = _run_labs(monkeypatch, capsys, old_log, ["labs-unit"], "passed")
    assert "performance attempt" not in out


def test_attempts_are_printed_when_this_run_wrote_the_log(monkeypatch, capsys, tmp_path) -> None:
    log = tmp_path / "perf.json"

    def fake_run_check(check, **kwargs):
        if check.check_id == "labs-performance":
            time.sleep(0.05)  # file timestamps can lag time.time() by a few ms on Windows
            log.write_text(json.dumps(_LOG), encoding="utf-8")
        check.status = "passed"
        check.detail = "fake"

    monkeypatch.setattr(rv, "run_check", fake_run_check)
    rv.main(["--suite", "labs", "--perf-log", str(log), "--only", "labs-performance"])
    out = capsys.readouterr().out
    assert _ATTEMPT_LINE in out
    assert "not written by this run" not in out


def test_perf_output_lines_rules(old_log: Path) -> None:
    perf = rv.phase_report.load_perf_log(old_log)
    assert rv.perf_output_lines(False, old_log, 0.0, perf) == []
    stale = rv.perf_output_lines(True, old_log, time.time(), perf)
    assert len(stale) == 1 and "not written by this run" in stale[0]
    missing = rv.perf_output_lines(True, old_log.with_name("none.json"), 0.0, None)
    assert len(missing) == 1 and "not written by this run" in missing[0]
    fresh = rv.perf_output_lines(True, old_log, 0.0, perf)
    assert any(_ATTEMPT_LINE in line for line in fresh)


def test_docstring_puts_labs_performance_in_phase_two() -> None:
    doc = rv.__doc__ or ""
    assert "--only labs-playwright-real labs-performance" in doc
    assert "--only labs-playwright-real\n" not in doc
