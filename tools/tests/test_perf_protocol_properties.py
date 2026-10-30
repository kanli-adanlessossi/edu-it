"""Property test for the performance measurement protocol (hands-on-labs, task 22.5).

Feature: hands-on-labs, Property 38: Performance measurement protocol

Over sequences of attempts with raw samples: the first 3 samples are discarded, an attempt is
valid only with at least 30 counted start-to-ready and 200 counted keystroke samples, at most one
re-measurement follows a miss, every attempt appears in the log, the decision uses only the final
attempt, and the report writer produces a report iff the final attempt meets every target.

The Requirement 33 mapping, checks and review outcome are injected fakes, so this test does not
depend on the labs traceability suite.

**Validates: Requirements 31.4, 31.5, 31.6, 31.7, 31.8, 35.3, 35.4**
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import phase_report as pr  # noqa: E402

protocol = pr.protocol

TARGETS = (protocol.START_TO_READY, protocol.KEYSTROKE, protocol.SHELL_CHECK)
LIMITS = {protocol.START_TO_READY: 20.0, protocol.KEYSTROKE: 0.150, protocol.SHELL_CHECK: 15.0}
MINIMUMS = {protocol.START_TO_READY: 30, protocol.KEYSTROKE: 200, protocol.SHELL_CHECK: 1}
RULE_MAP = [("Rule A", ["31.4"], [("tools/tests/x.py", "needle")])]


@st.composite
def _samples(draw, target: str) -> list[float]:
    required = MINIMUMS[target] + 3
    size = draw(st.one_of(st.just(required), st.integers(0, required + 3)))
    limit = LIMITS[target]
    high = draw(st.sampled_from([limit * 0.9, limit * 0.9, limit * 1.5]))
    return draw(
        st.lists(
            st.floats(0, high, allow_nan=False, allow_infinity=False),
            min_size=size,
            max_size=size,
        )
    )


@st.composite
def _scripts(draw) -> list[dict[str, list[float]]]:
    """Raw samples the driver would return for attempt 1 and (if needed) the re-measurement."""
    return [{t: draw(_samples(t)) for t in TARGETS} for _ in range(2)]


def _oracle_met(target: str, raw: list[float]) -> bool:
    counted = raw[3:]
    if len(counted) < MINIMUMS[target]:
        return False
    if target == protocol.SHELL_CHECK:
        return max(counted) < LIMITS[target]
    rank = (95 * len(counted) + 99) // 100  # ceil(0.95 * n) in integers
    return sorted(counted)[rank - 1] < LIMITS[target]


def _run(scripts):
    calls: dict[str, int] = {t: 0 for t in TARGETS}
    requested: list[tuple[str, int]] = []

    def collect(target: str, count: int) -> list[float]:
        requested.append((target, count))
        index = calls[target]
        calls[target] += 1
        return scripts[index][target]

    return protocol.run_protocol(collect), requested


def _perf_log(outcome) -> dict:
    return protocol.outcome_to_dict(outcome, {"cpu": "fake"})


def _checks() -> list[pr.CheckRecord]:
    return [pr.CheckRecord("labs-unit", "5", "unit", "pytest -q", "passed", "ok")]


def _groups() -> dict[str, pr.GroupRecord]:
    groups = pr.group_mapping(RULE_MAP)
    for group in groups.values():
        group.status = "passed"
    return groups


def _review() -> pr.ReviewOutcome:
    return pr.ReviewOutcome(
        labs={"lab.a": {"reviewed_by": "someone", "reviewed_at": "2026-11-02T09:00:00Z"}},
        unreviewed=[],
        pending_ids=[],
        strict_validation="passed",
        approval_flow="passed",
    )


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example, HealthCheck.data_too_large],
)
@given(scripts=_scripts())
def test_property_38_performance_measurement_protocol(scripts):
    # Feature: hands-on-labs, Property 38: Performance measurement protocol
    outcome, requested = _run(scripts)

    # At most one re-measurement, and only after a miss (31.6, 31.7).
    expected_met = [all(_oracle_met(t, s[t]) for t in TARGETS) for s in scripts]
    assert len(outcome.attempts) == (1 if expected_met[0] else 2)
    assert len(outcome.attempts) <= protocol.MAX_ATTEMPTS
    assert len(requested) == 3 * len(outcome.attempts)

    for index, attempt in enumerate(outcome.attempts):
        for target in TARGETS:
            raw = scripts[index][target]
            result = attempt.results[target]
            # The first 3 samples are discarded; validity needs the minimum counted (31.4).
            assert result.counted_values == raw[3:]
            assert result.enough_samples == (len(raw[3:]) >= MINIMUMS[target])
            assert result.met == _oracle_met(target, raw)
            if not result.enough_samples:
                assert not result.met

    # The decision uses only the final attempt (31.8).
    final = len(outcome.attempts) - 1
    assert outcome.passed == expected_met[final]

    # Every attempt appears in the log, the failed one included (31.8, 35.4).
    log = _perf_log(outcome)
    assert [a["attempt"] for a in log["attempts"]] == list(range(1, len(outcome.attempts) + 1))
    assert len(pr.format_perf_attempts(log)) == 3 * len(outcome.attempts) + 1
    assert pr.perf_blockers(log) == [] if outcome.passed else pr.perf_blockers(log) != []

    # The report writer writes iff the final attempt meets every target (31.5, 35.3).
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reports" / "phase-3.md"
        args = dict(
            path=path,
            checks=_checks(),
            perf=log,
            rule_map=RULE_MAP,
            groups=_groups(),
            review=_review(),
            required_check_ids=["labs-unit"],
        )
        if outcome.passed:
            pr.write_phase_report(**args)
            assert path.is_file()
            text = path.read_text(encoding="utf-8")
            assert f"attempt {len(outcome.attempts)}" in text
        else:
            with pytest.raises(pr.PhaseReportRefused):
                pr.write_phase_report(**args)
            assert not path.exists() and not path.parent.exists()
