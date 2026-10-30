"""Unit tests for the driver-reported sample parsing in ``tools/perf/lab_perf.py`` (task 21.5)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))
import lab_perf  # noqa: E402


def test_last_json_line_with_seconds_is_the_sample() -> None:
    out = 'noise\n{"seconds": 0.25, "session": "s"}\n'
    assert lab_perf.reported_seconds(out) == 0.25


def test_no_report_falls_back_to_wall_clock() -> None:
    assert lab_perf.reported_seconds("") is None
    assert lab_perf.reported_seconds("plain text\n{not json}\n") is None


def test_invalid_values_are_ignored() -> None:
    assert lab_perf.reported_seconds('{"seconds": -1}') is None
    assert lab_perf.reported_seconds('{"seconds": true}') is None
    assert lab_perf.reported_seconds('{"seconds": "1"}') is None
