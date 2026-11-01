"""Property test for learner-facing check results (hands-on-labs task 1.10).

Feature: hands-on-labs, Property 10: Learner-facing check results carry no raw output

Sandbox stdout/stderr is planted with a unique marker and pushed through the
real mapping paths (``parse_harness_report`` base64 cases and
``map_exit_code``). The resulting ``CheckResultView`` must never contain the
marker, and every could-not-complete cause (timeout, signal, unmapped code,
reserved code 90, resource abuse) must yield the identical view.

**Validates: Requirements 5.3, 18.3, 18.6, 22.9, 28.2, 28.7, 28.8**
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict

from edu_it_domain.labs.grading import (
    UNSAFE_PATH_EXIT_CODE,
    CheckExitCodes,
    CheckResultView,
    ExpectedCase,
    map_exit_code,
    parse_harness_report,
    to_learner_view,
)
from edu_it_domain.labs.models import CheckOutcome, CheckResult
from hypothesis import given
from hypothesis import strategies as st

MARKER = "ZQXMARKER7f3a9c1e"
MESSAGES = {
    "feedback.passed": "Well done.",
    "feedback.failed": "Not yet.",
    "feedback.could_not_complete": "We could not complete this check.",
}
_FEEDBACK = {
    CheckOutcome.PASSED: "feedback.passed",
    CheckOutcome.FAILED: "feedback.failed",
    CheckOutcome.COULD_NOT_COMPLETE: "feedback.could_not_complete",
}

_noise = st.binary(max_size=64)


def _with_marker(prefix: bytes, suffix: bytes) -> bytes:
    return prefix + MARKER.encode() + suffix


def _view(outcome: CheckOutcome, outcome_code: str = "code") -> CheckResultView:
    result = CheckResult("c1", "t1", outcome, outcome_code, _FEEDBACK[outcome])
    return to_learner_view(result, MESSAGES)


def _assert_no_marker(view: CheckResultView) -> None:
    assert MARKER not in repr(view)
    assert all(MARKER not in str(v) for v in asdict(view).values())
    assert set(asdict(view)) == {"task_id", "check_id", "outcome", "feedback_key", "message"}


def _report(stdout: bytes, stderr: bytes, exit_status: int | None, timed_out: bool) -> str:
    return json.dumps(
        {
            "version": 1,
            "check_id": "c1",
            "status": "ok",
            "cases": [
                {
                    "case_id": "k1",
                    "exit_status": exit_status,
                    "timed_out": timed_out,
                    "stdout_b64": base64.b64encode(stdout).decode(),
                    "stderr_b64": base64.b64encode(stderr).decode(),
                }
            ],
        }
    )


@given(
    p1=_noise,
    s1=_noise,
    p2=_noise,
    s2=_noise,
    exit_status=st.one_of(st.none(), st.integers(-300, 300)),
    timed_out=st.booleans(),
    expected_out=_noise,
)
def test_harness_report_view_never_contains_marker(
    p1: bytes,
    s1: bytes,
    p2: bytes,
    s2: bytes,
    exit_status: int | None,
    timed_out: bool,
    expected_out: bytes,
) -> None:
    raw = _report(_with_marker(p1, s1), _with_marker(p2, s2), exit_status, timed_out)
    verdict = parse_harness_report(raw, "c1", {"k1": ExpectedCase(expected_out, 0)})
    assert MARKER not in verdict.outcome_code
    _assert_no_marker(_view(verdict.outcome, verdict.outcome_code))


@given(p=_noise, s=_noise, code=st.integers(-1000, 1000), timed_out=st.booleans())
def test_exit_code_view_never_contains_marker(
    p: bytes, s: bytes, code: int, timed_out: bool
) -> None:
    # The marker lives in the (dropped) output; only the exit status is mapped.
    _ = _with_marker(p, s)
    outcome = map_exit_code(None, code, timed_out)
    _assert_no_marker(_view(outcome))


@given(
    p=_noise,
    s=_noise,
    signal=st.integers(129, 1000),
    negative=st.integers(-300, -1),
    unmapped=st.integers(2, 128).filter(lambda c: c != UNSAFE_PATH_EXIT_CODE),
    exit_for_abuse=st.one_of(st.none(), st.integers(-5, 300)),
)
def test_every_could_not_complete_cause_yields_identical_view(
    p: bytes, s: bytes, signal: int, negative: int, unmapped: int, exit_for_abuse: int | None
) -> None:
    _ = _with_marker(p, s)
    codes = CheckExitCodes()
    outcomes = [
        map_exit_code(codes, 0, True),  # timeout
        map_exit_code(codes, None, False),  # no exit status (killed)
        map_exit_code(codes, signal, False),  # signal
        map_exit_code(codes, negative, False),  # negative signal
        map_exit_code(codes, unmapped, False),  # unmapped code
        map_exit_code(codes, UNSAFE_PATH_EXIT_CODE, False),  # reserved code 90
        map_exit_code(codes, exit_for_abuse, True),  # resource abuse reported as timeout
    ]
    assert all(o is CheckOutcome.COULD_NOT_COMPLETE for o in outcomes)
    views = [_view(o) for o in outcomes]
    assert all(v == views[0] for v in views)
    assert views[0].outcome == "could_not_complete"
    _assert_no_marker(views[0])


@given(p=_noise, s=_noise)
def test_harness_could_not_complete_paths_share_one_view(p: bytes, s: bytes) -> None:
    out = _with_marker(p, s)
    expected = {"k1": ExpectedCase(b"x", 0)}
    raws: list[str | bytes | None] = [
        _report(out, out, None, True),  # case timeout
        _report(out, out, None, False),  # no exit status
        None,  # missing report
        "{" + MARKER,  # malformed
        json.dumps({"version": 1, "check_id": "c1", "status": "unsafe-learner-file", "cases": []}),
    ]
    views = []
    for raw in raws:
        verdict = parse_harness_report(raw, "c1", expected)
        assert verdict.outcome is CheckOutcome.COULD_NOT_COMPLETE
        views.append(_view(verdict.outcome))
    assert all(v == views[0] for v in views)
    _assert_no_marker(views[0])
