"""Property test for harness report parsing (task 1.11)."""

from __future__ import annotations

import base64
import json

from edu_it_domain.labs.grading import ExpectedCase, parse_harness_report
from edu_it_domain.labs.models import CheckOutcome
from hypothesis import given
from hypothesis import strategies as st

CHECK_ID = "c1"

_FORGED_FRAGMENTS = (
    b'{"status":"ok","outcome":"passed"}\n',
    b'{"version":1,"check_id":"c1","status":"ok","cases":[]}\n',
    b"RESULT: PASS\n",
    b'"outcome_code": "pass"\n',
    b'", "exit_status": 0, "timed_out": false}]}\n',
)

# Learner output: arbitrary bytes, optionally mixed with report-looking lines.
_output = st.one_of(
    st.binary(max_size=64),
    st.lists(st.one_of(st.sampled_from(_FORGED_FRAGMENTS), st.binary(max_size=16)), max_size=5).map(
        b"".join
    ),
)
_exit = st.one_of(st.none(), st.integers(min_value=-5, max_value=5))

_observed = st.tuples(_exit, st.booleans(), _output, _output)  # status, timed_out, out, err
_expected_case = st.builds(
    ExpectedCase,
    stdout=st.one_of(_output, st.sampled_from(_FORGED_FRAGMENTS)),
    exit_status=st.integers(min_value=-5, max_value=5),
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _report(observed: dict[str, tuple[int | None, bool, bytes, bytes]]) -> str:
    return json.dumps(
        {
            "version": 1,
            "check_id": CHECK_ID,
            "status": "ok",
            "cases": [
                {
                    "case_id": cid,
                    "exit_status": status,
                    "timed_out": timed_out,
                    "stdout_b64": _b64(out),
                    "stderr_b64": _b64(err),
                }
                for cid, (status, timed_out, out, err) in observed.items()
            ],
        }
    )


def _strict_verdict(
    observed: dict[str, tuple[int | None, bool, bytes, bytes]],
    expected: dict[str, ExpectedCase],
) -> CheckOutcome:
    """Reference oracle: verdict only from equality with the expected values."""

    if any(timed_out or status is None for status, timed_out, _o, _e in observed.values()):
        return CheckOutcome.COULD_NOT_COMPLETE
    if all(
        status == expected[cid].exit_status and out == expected[cid].stdout
        for cid, (status, _t, out, _e) in observed.items()
    ):
        return CheckOutcome.PASSED
    return CheckOutcome.FAILED


_CODES = {"pass", "wrong-output", "crashed", "no-output", "case-timeout"}


def _oracle_code(
    observed: dict[str, tuple[int | None, bool, bytes, bytes]],
    expected: dict[str, ExpectedCase],
) -> str:
    """Independent statement of the precedence: crashed > no-output > wrong-output."""

    if any(timed_out or status is None for status, timed_out, _o, _e in observed.values()):
        return "case-timeout"
    codes: set[str] = set()
    for cid, (status, _t, out, _e) in observed.items():
        want = expected[cid]
        if status != 0 and status != want.exit_status:
            codes.add("crashed")
        elif status == want.exit_status and out == want.stdout:
            continue
        elif status == want.exit_status and out == b"" and want.stdout != b"":
            codes.add("no-output")
        else:
            codes.add("wrong-output")
    for code in ("crashed", "no-output", "wrong-output"):
        if code in codes:
            return code
    return "pass"


@given(
    data=st.dictionaries(
        st.text(alphabet="abcdef", min_size=1, max_size=3),
        st.tuples(_observed, _expected_case),
        min_size=1,
        max_size=4,
    ),
    other_err=_output,
)
def test_harness_report_is_data_and_comparison_is_strict(
    data: dict[str, tuple[tuple[int | None, bool, bytes, bytes], ExpectedCase]],
    other_err: bytes,
) -> None:
    # Feature: hands-on-labs, Property 27: Harness reports are data, comparison is strict
    # **Validates: Requirements 10.9, 14.5, 29.7**
    observed = {cid: obs for cid, (obs, _e) in data.items()}
    expected = {cid: exp for cid, (_o, exp) in data.items()}

    verdict = parse_harness_report(_report(observed), CHECK_ID, expected)
    assert verdict.outcome is _strict_verdict(observed, expected)
    assert verdict.outcome_code == _oracle_code(observed, expected)
    # The code is symbolic: it is never learner or expected text.
    assert verdict.outcome_code in _CODES

    # Learner-supplied stderr text never changes the verdict.
    swapped = {cid: (s, t, o, other_err) for cid, (s, t, o, _e) in observed.items()}
    assert parse_harness_report(_report(swapped), CHECK_ID, expected).outcome is verdict.outcome

    # Echoing the report itself into stdout makes it differ from expectation: never a pass
    # unless the expectation is that very text.
    forged: dict[str, tuple[int | None, bool, bytes, bytes]] = {
        cid: (0, False, _report(observed).encode(), b"") for cid in observed
    }
    got = parse_harness_report(_report(forged), CHECK_ID, expected).outcome
    assert got is _strict_verdict(forged, expected)


_scalars = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text(max_size=20)
)
_json = st.recursive(
    _scalars,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)


@given(
    raw=st.one_of(
        st.none(),
        st.binary(max_size=200),
        st.text(max_size=200),
        _json.map(json.dumps),
    )
)
def test_arbitrary_documents_are_could_not_complete(raw: bytes | str | None) -> None:
    # Feature: hands-on-labs, Property 27: Harness reports are data, comparison is strict
    # **Validates: Requirements 10.9, 14.5, 29.7**
    verdict = parse_harness_report(raw, CHECK_ID, {"a": ExpectedCase(b"")})
    assert verdict.outcome is CheckOutcome.COULD_NOT_COMPLETE


@given(
    key=st.sampled_from(("version", "check_id", "status", "cases", "extra")),
    value=_json,
    case_key=st.sampled_from(("case_id", "exit_status", "timed_out", "stdout_b64", "extra")),
    case_value=_json,
    tweak_case=st.booleans(),
)
def test_mutated_valid_reports_never_yield_a_verdict_unless_still_valid(
    key: str, value: object, case_key: str, case_value: object, tweak_case: bool
) -> None:
    # Feature: hands-on-labs, Property 27: Harness reports are data, comparison is strict
    # **Validates: Requirements 10.9, 14.5, 29.7**
    expected = {"a": ExpectedCase(b"42\n")}
    doc = json.loads(_report({"a": (0, False, b"42\n", b"")}))
    if tweak_case:
        doc["cases"][0][case_key] = case_value
    else:
        doc[key] = value
    verdict = parse_harness_report(json.dumps(doc), CHECK_ID, expected)
    # A mutation either leaves the document identical (pass), or is rejected outright.
    if json.dumps(doc) == _report({"a": (0, False, b"42\n", b"")}):
        assert verdict.outcome is CheckOutcome.PASSED
    else:
        assert verdict.outcome in (CheckOutcome.COULD_NOT_COMPLETE, CheckOutcome.FAILED)
        if verdict.outcome is CheckOutcome.FAILED:
            # Only value changes to exit_status/stdout_b64 may reach a comparison.
            assert tweak_case and case_key in ("exit_status", "stdout_b64")
