"""Tests for pure grading rules (hands-on-labs task 1.6)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from edu_it_domain import config
from edu_it_domain.labs.grading import (
    ExpectedCase,
    PsRow,
    aggregate_run,
    effective_timeout,
    learner_all_stopped,
    map_exit_code,
    parse_harness_report,
    remaining_budget,
    to_learner_view,
)
from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus
from hypothesis import given
from hypothesis import strategies as st

T0 = datetime(2025, 1, 1, tzinfo=UTC)
S = timedelta(seconds=1)


def _result(outcome: CheckOutcome) -> CheckResult:
    return CheckResult("c1", "t1", outcome, "code", "fb.key")


def test_budget_deducts_queue_delay_and_spent() -> None:
    assert remaining_budget(T0, T0, timedelta(0), config) == 12 * S
    assert remaining_budget(T0, T0 + 3 * S, 2 * S, config) == 7 * S
    assert remaining_budget(T0, T0 + 20 * S, timedelta(0), config) < timedelta(0)


def test_effective_timeout() -> None:
    assert effective_timeout(5 * S, 3 * S) == 3 * S
    assert effective_timeout(5 * S, 9 * S) == 5 * S
    assert effective_timeout(5 * S, timedelta(0)) <= timedelta(0)


def test_learner_all_stopped() -> None:
    rows = [PsRow("learner", "T"), PsRow("learner", "Z"), PsRow("root", "S")]
    assert learner_all_stopped(rows)
    assert not learner_all_stopped([*rows, PsRow("learner", "S")])
    assert not learner_all_stopped([PsRow("learner", "")])
    assert learner_all_stopped([])


@given(st.integers(min_value=-300, max_value=300), st.booleans())
def test_map_exit_code_only_two_codes_give_verdicts(code: int, timed_out: bool) -> None:
    out = map_exit_code(None, code, timed_out)
    if timed_out or code not in (0, 1):
        assert out is CheckOutcome.COULD_NOT_COMPLETE
    else:
        assert out is (CheckOutcome.PASSED if code == 0 else CheckOutcome.FAILED)


def test_map_exit_code_reserved_and_missing() -> None:
    assert map_exit_code(None, 90, False) is CheckOutcome.COULD_NOT_COMPLETE
    assert map_exit_code(None, None, False) is CheckOutcome.COULD_NOT_COMPLETE


def test_aggregate_run() -> None:
    p, f, c = (_result(o) for o in CheckOutcome)
    assert aggregate_run([p, p], False) is RunStatus.PASSED
    assert aggregate_run([p, f], False) is RunStatus.FAILED
    assert aggregate_run([p, c], False) is RunStatus.FAILED
    assert aggregate_run([p, p], True) is RunStatus.COULD_NOT_COMPLETE
    assert aggregate_run([], False) is RunStatus.COULD_NOT_COMPLETE


def test_learner_view_has_only_safe_fields() -> None:
    view = to_learner_view(_result(CheckOutcome.FAILED), {"fb.key": "Try again"})
    assert (view.task_id, view.check_id, view.outcome, view.message) == (
        "t1",
        "c1",
        "failed",
        "Try again",
    )
    assert to_learner_view(_result(CheckOutcome.FAILED), {}).message == ""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _report(stdout: bytes, exit_status: int | None = 0, **over: object) -> str:
    doc: dict[str, object] = {
        "version": 1,
        "check_id": "c1",
        "status": "ok",
        "cases": [
            {
                "case_id": "a",
                "exit_status": exit_status,
                "timed_out": False,
                "stdout_b64": _b64(stdout),
                "stderr_b64": _b64(b""),
            }
        ],
    }
    doc.update(over)
    return json.dumps(doc)


EXPECTED = {"a": ExpectedCase(b"42\n")}


def test_report_pass_fail() -> None:
    assert parse_harness_report(_report(b"42\n"), "c1", EXPECTED).outcome is CheckOutcome.PASSED
    assert parse_harness_report(_report(b"41\n"), "c1", EXPECTED).outcome is CheckOutcome.FAILED
    assert parse_harness_report(_report(b"42\n", 1), "c1", EXPECTED).outcome is CheckOutcome.FAILED


def _code(stdout: bytes, exit_status: int | None = 0, want: ExpectedCase | None = None) -> str:
    expected = {"a": want or ExpectedCase(b"42\n")}
    return parse_harness_report(_report(stdout, exit_status), "c1", expected).outcome_code


def test_failure_codes_crashed_no_output_wrong_output() -> None:
    assert _code(b"42\n") == "pass"
    assert _code(b"41\n") == "wrong-output"
    assert _code(b"", 0) == "no-output"
    assert _code(b"", 1) == "crashed"  # silent *and* non-zero: crash wins
    assert _code(b"41\n", 1) == "crashed"  # partial output, then an uncaught exception
    assert _code(b"42\n", 1) == "crashed"  # right output, wrong exit status


def test_empty_stdout_is_wrong_output_when_output_is_not_expected() -> None:
    # Nothing was expected, so the only possible difference is the exit status or extra output.
    none_expected = ExpectedCase(b"")
    assert _code(b"x\n", 0, none_expected) == "wrong-output"
    assert _code(b"", 3, none_expected) == "crashed"


def test_expected_non_zero_exit_is_not_a_crash() -> None:
    want = ExpectedCase(b"", exit_status=2)
    assert _code(b"", 2, want) == "pass"
    assert _code(b"", 0, want) == "wrong-output"  # exited 0 where 2 was expected
    assert _code(b"", 5, want) == "crashed"


def test_failure_precedence_across_cases_is_crashed_then_no_output_then_wrong_output() -> None:
    expected = {n: ExpectedCase(b"42\n") for n in "abc"}

    def report(*cases: tuple[bytes, int]) -> str:
        doc = json.loads(_report(b""))
        doc["cases"] = [
            {
                "case_id": cid,
                "exit_status": status,
                "timed_out": False,
                "stdout_b64": _b64(out),
                "stderr_b64": _b64(b""),
            }
            for cid, (out, status) in zip("abc", cases, strict=True)
        ]
        return json.dumps(doc)

    def code(*cases: tuple[bytes, int]) -> str:
        return parse_harness_report(report(*cases), "c1", expected).outcome_code

    ok, wrong, silent, crash = (b"42\n", 0), (b"41\n", 0), (b"", 0), (b"", 1)
    assert code(ok, ok, ok) == "pass"
    assert code(ok, wrong, silent) == "no-output"
    assert code(wrong, silent, crash) == "crashed"
    assert code(crash, ok, wrong) == "crashed"
    assert code(wrong, ok, ok) == "wrong-output"


def test_failure_codes_never_carry_expected_output() -> None:
    secret = b"TOP-SECRET-EXPECTED\n"
    for out, status in ((b"x", 0), (b"", 0), (b"", 1)):
        verdict = parse_harness_report(_report(out, status), "c1", {"a": ExpectedCase(secret)})
        assert verdict.outcome is CheckOutcome.FAILED
        assert verdict.outcome_code in {"wrong-output", "no-output", "crashed"}
        assert "SECRET" not in verdict.outcome_code


def test_report_malformed_or_missing() -> None:
    bad = [None, "", "not json", "[]", _report(b"42\n", status="weird"), _report(b"42\n", extra=1)]
    bad += [_report(b"42\n", version=2), _report(b"42\n", check_id="other"), _report(b"x", None)]
    for raw in bad:
        got = parse_harness_report(raw, "c1", EXPECTED).outcome
        assert got is CheckOutcome.COULD_NOT_COMPLETE, raw


def test_report_unsafe_learner_file() -> None:
    raw = _report(b"", status="unsafe-learner-file", cases=[])
    v = parse_harness_report(raw, "c1", EXPECTED)
    assert (v.outcome, v.outcome_code) == (CheckOutcome.COULD_NOT_COMPLETE, "unsafe-learner-file")
