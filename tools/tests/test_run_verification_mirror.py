"""run_verification passes ALPINE_MIRROR to child checks only when non-empty (api-route-wiring 13.4;
Requirement 17.3), the same rule as tools/e2e_stack.py up."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_verification as rv


def _captured_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(rv.subprocess, "run", fake_run)
    check = rv.Check("x", "4", "d", ["python", "-c", "pass"])
    rv.run_check(check)
    assert check.status == "passed"
    return seen


def test_mirror_passed_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPINE_MIRROR", "https://m.example/alpine")
    assert _captured_env(monkeypatch)["ALPINE_MIRROR"] == "https://m.example/alpine"


@pytest.mark.parametrize("value", [None, ""])
def test_mirror_absent_when_unset_or_empty(monkeypatch: pytest.MonkeyPatch, value) -> None:
    if value is None:
        monkeypatch.delenv("ALPINE_MIRROR", raising=False)
    else:
        monkeypatch.setenv("ALPINE_MIRROR", value)
    assert "ALPINE_MIRROR" not in _captured_env(monkeypatch)
