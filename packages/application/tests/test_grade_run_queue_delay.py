"""Queue-delay tests for GradeRun (task 8.11).

**Validates: Requirements 11.8, 11.9**
"""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

from edu_it_domain import config
from edu_it_domain.labs.models import CheckOutcome, RunStatus

# Reuse the helpers of test_grade_run.py (tests dir is not a package).
_spec = importlib.util.spec_from_file_location(
    "_grade_run_helpers", Path(__file__).with_name("test_grade_run.py")
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
_CONT, _STOP, _Env, _lab = _helpers._CONT, _helpers._STOP, _helpers._Env, _helpers._lab

_BUDGET_S = (
    config.LAB_GRADING_RUN_DEADLINE
    - config.LAB_GRADING_FREEZE_BOUND
    - config.LAB_GRADING_TEARDOWN_RESERVE
).total_seconds()


def test_usable_budget_is_twelve_seconds() -> None:
    assert _BUDGET_S == 12.0


def test_delay_below_budget_runs_checks_within_reduced_budget(tmp_path: Path) -> None:
    env = _Env(tmp_path, _lab(timeout_s=60.0))
    delay = 5.0
    env.clock.advance(timedelta(seconds=delay))
    seen: list[float | None] = []
    original = env.sandbox.exec

    def spy(handle, command, **kw):
        if command[0] == "/bin/sh":
            seen.append(kw["timeout_s"])
        return original(handle, command, **kw)

    env.sandbox.exec = spy  # type: ignore[method-assign]
    assert env.job.execute("r1") is RunStatus.PASSED
    assert seen, "checks should run when budget remains"
    assert all(0 < t <= _BUDGET_S - delay for t in seen if t is not None)
    assert seen[0] == _BUDGET_S - delay
    assert "copy_in" in env.sandbox.calls
    assert set(env.outcomes().values()) == {CheckOutcome.PASSED}


@pytest.mark.parametrize("delay", [_BUDGET_S, _BUDGET_S + 0.5, 14.0])
def test_delay_at_or_over_budget_runs_nothing(tmp_path: Path, delay: float) -> None:
    env = _Env(tmp_path, _lab(timeout_s=60.0))
    env.script(lambda *_: None)
    env.clock.advance(timedelta(seconds=delay))
    assert env.job.execute("r1") is RunStatus.COULD_NOT_COMPLETE
    assert "copy_in" not in env.sandbox.calls
    assert not [c for c in env.commands if c[0] == _STOP]
    assert not [c for c in env.commands if c[0][:1] == ["/bin/sh"]]
    assert set(env.outcomes().values()) == {CheckOutcome.COULD_NOT_COMPLETE}
    assert len(env.run().results) == 2
    assert env.mastery.get("u1", "cn1") is None
    assert [c for c in env.commands if c[0] == _CONT]  # thaw still attempted
