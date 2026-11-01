"""Pure grading rules (hands-on-labs Req 10.7-10.9, 11.2-11.4, 11.8, 14.5, 18.3, 28, 29.7, 29.9).

No I/O and no clock. Nothing here trusts learner-controlled text: exit codes
and harness reports are mapped to an outcome, and the raw material is dropped.
``config`` is either a mapping of key to value or any object exposing the keys
as attributes (for example the ``config`` module).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus

__all__ = [
    "DEFAULT_EXIT_CODES",
    "HARNESS_REPORT_VERSION",
    "UNSAFE_PATH_EXIT_CODE",
    "CheckExitCodes",
    "CheckResultView",
    "ExpectedCase",
    "HarnessVerdict",
    "PsRow",
    "aggregate_run",
    "effective_timeout",
    "learner_all_stopped",
    "map_exit_code",
    "parse_harness_report",
    "remaining_budget",
    "to_learner_view",
]

#: Reserved Check exit code: a symlink, FIFO or device sat where a regular
#: file or directory was expected (Req 29.9). Always ``could_not_complete``.
UNSAFE_PATH_EXIT_CODE: Final[int] = 90

#: Schema version of the in-sandbox harness report.
HARNESS_REPORT_VERSION: Final[int] = 1


def _cfg(config: object, key: str) -> Any:
    if isinstance(config, Mapping):
        return config[key]
    return getattr(config, key)


# --- Time budget -------------------------------------------------------------


def remaining_budget(
    accepted_at: datetime,
    job_started_at: datetime,
    spent_on_checks: timedelta,
    config: object,
) -> timedelta:
    """Usable_Run_Budget minus queue delay minus time spent on Checks (Req 11.8).

    Queue delay is measured from ``accepted_at``, not from the job start. The
    result may be zero or negative, which means "run nothing".
    """

    usable: timedelta = (
        _cfg(config, "LAB_GRADING_RUN_DEADLINE")
        - _cfg(config, "LAB_GRADING_FREEZE_BOUND")
        - _cfg(config, "LAB_GRADING_TEARDOWN_RESERVE")
    )
    queue_delay = max(job_started_at - accepted_at, timedelta(0))
    return usable - queue_delay - spent_on_checks


def effective_timeout(check_timeout: timedelta, remaining: timedelta) -> timedelta:
    """``min(check_timeout, remaining)``; a non-positive result means do not run."""

    return min(check_timeout, remaining)


# --- Freeze verification -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PsRow:
    """One row of the in-sandbox process listing: owner and state letters."""

    user: str
    state: str


#: Process state letters that count as stopped: stopped, traced, zombie, dead.
_STOPPED_STATES: Final[frozenset[str]] = frozenset({"T", "t", "Z", "X"})


def learner_all_stopped(ps_rows: Iterable[PsRow], learner_user: str = "learner") -> bool:
    """True iff no learner-owned process is in a running state (Req 10.7, 28.2).

    Stopped (``T``/``t``) and zombie (``Z``/``X``) rows count as stopped. An
    unrecognized or empty state is treated as running, so verification fails
    closed.
    """

    for row in ps_rows:
        if row.user != learner_user:
            continue
        state = row.state.strip()
        if not state or state[0] not in _STOPPED_STATES:
            return False
    return True


# --- Exit codes and run aggregation -------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckExitCodes:
    """The exit codes a shell Check uses to report a verdict."""

    passed: int = 0
    failed: int = 1


DEFAULT_EXIT_CODES: Final[CheckExitCodes] = CheckExitCodes()


def map_exit_code(
    check: CheckExitCodes | None,
    exit_code: int | None,
    timed_out: bool,
) -> CheckOutcome:
    """Map a shell Check's exit status to an outcome (Req 11.2, 11.3, 28.5).

    Only the two declared codes yield a verdict. A timeout, a missing code, a
    signal (negative or above 128), the reserved code 90 and any unknown code
    become ``could_not_complete``.
    """

    codes = check or DEFAULT_EXIT_CODES
    if timed_out or exit_code is None or exit_code == UNSAFE_PATH_EXIT_CODE:
        return CheckOutcome.COULD_NOT_COMPLETE
    if exit_code == codes.passed:
        return CheckOutcome.PASSED
    if exit_code == codes.failed:
        return CheckOutcome.FAILED
    return CheckOutcome.COULD_NOT_COMPLETE


def aggregate_run(results: Sequence[CheckResult], abort: bool) -> RunStatus:
    """Aggregate Check outcomes into a run status (Req 10.8, 28.8, 29.9).

    ``abort`` (vanished Sandbox, unverified freeze, unsafe learner file) gives
    ``could-not-complete``. Otherwise the run passes iff there is at least one
    Check and every Check passed; an empty run never passes.
    """

    if abort or not results:
        return RunStatus.COULD_NOT_COMPLETE
    if all(r.outcome is CheckOutcome.PASSED for r in results):
        return RunStatus.PASSED
    return RunStatus.FAILED


@dataclass(frozen=True, slots=True)
class CheckResultView:
    """The only per-Check shape that leaves the worker (Req 11.4, 18.3)."""

    task_id: str
    check_id: str
    outcome: str
    feedback_key: str
    message: str


def to_learner_view(result: CheckResult, messages: Mapping[str, str]) -> CheckResultView:
    """Project a result to ids, outcome, feedback key and translated message.

    ``messages`` maps feedback keys to the already-localized text. A missing
    key yields an empty message, never learner or sandbox text.
    """

    return CheckResultView(
        task_id=result.task_id,
        check_id=result.check_id,
        outcome=result.outcome.value,
        feedback_key=result.feedback_key,
        message=messages.get(result.feedback_key, ""),
    )


# --- Harness report ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpectedCase:
    """Expected observation for one case; held outside the Sandbox (Req 14.5)."""

    stdout: bytes
    exit_status: int = 0


@dataclass(frozen=True, slots=True)
class HarnessVerdict:
    """Outcome of a Code Check plus a symbolic, learner-safe outcome code."""

    outcome: CheckOutcome
    outcome_code: str


_CNC_MALFORMED: Final = HarnessVerdict(CheckOutcome.COULD_NOT_COMPLETE, "malformed-report")
_TOP_KEYS: Final = frozenset({"version", "check_id", "status", "cases"})
_CASE_KEYS: Final = frozenset({"case_id", "exit_status", "timed_out", "stdout_b64", "stderr_b64"})


def _b64(value: object) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        return None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_harness_report(
    raw: bytes | str | None,
    check_id: str,
    expected: Mapping[str, ExpectedCase],
) -> HarnessVerdict:
    """Strictly parse a harness report and compare it with expected values.

    Learner output only ever appears as base64 data and is compared byte for
    byte with the expectation; it is never interpreted, so text imitating a
    result line cannot change the verdict (Req 10.9, 29.7). A missing or
    malformed report, wrong version or check id, unknown keys, a case set that
    differs from ``expected``, or an ``unsafe-learner-file`` status all give
    ``could_not_complete``. A timed-out case is ``could_not_complete``.

    A failing run's ``outcome_code`` is the most severe class among its failing cases, by
    precedence ``crashed`` (unexpected non-zero exit) > ``no-output`` (expected output, empty
    stdout, expected exit) > ``wrong-output`` (any other difference). See ``_failure_rank``.
    """

    if raw is None:
        return HarnessVerdict(CheckOutcome.COULD_NOT_COMPLETE, "missing-report")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        return _CNC_MALFORMED
    if not isinstance(doc, dict) or set(doc) != _TOP_KEYS:
        return _CNC_MALFORMED
    if doc["version"] != HARNESS_REPORT_VERSION or not _is_int(doc["version"]):
        return _CNC_MALFORMED
    if doc["check_id"] != check_id:
        return _CNC_MALFORMED
    status = doc["status"]
    if status == "unsafe-learner-file":
        return HarnessVerdict(CheckOutcome.COULD_NOT_COMPLETE, "unsafe-learner-file")
    if status != "ok":
        return _CNC_MALFORMED
    cases = doc["cases"]
    if not isinstance(cases, list) or not cases:
        return _CNC_MALFORMED

    seen: dict[str, tuple[int | None, bool, bytes]] = {}
    for case in cases:
        if not isinstance(case, dict) or set(case) != _CASE_KEYS:
            return _CNC_MALFORMED
        case_id = case["case_id"]
        exit_status = case["exit_status"]
        timed_out = case["timed_out"]
        stdout = _b64(case["stdout_b64"])
        stderr = _b64(case["stderr_b64"])
        if (
            not isinstance(case_id, str)
            or case_id in seen
            or not isinstance(timed_out, bool)
            or not (exit_status is None or _is_int(exit_status))
            or stdout is None
            or stderr is None
        ):
            return _CNC_MALFORMED
        seen[case_id] = (exit_status, timed_out, stdout)

    if set(seen) != set(expected):
        return _CNC_MALFORMED

    worst = _RANK_PASS
    for case_id, (exit_status, timed_out, stdout) in seen.items():
        if timed_out or exit_status is None:
            return HarnessVerdict(CheckOutcome.COULD_NOT_COMPLETE, "case-timeout")
        worst = max(worst, _failure_rank(exit_status, stdout, expected[case_id]))
    if worst == _RANK_PASS:
        return HarnessVerdict(CheckOutcome.PASSED, "pass")
    return HarnessVerdict(CheckOutcome.FAILED, _FAILURE_CODES[worst])


# Failure classification of one case, ordered by precedence (higher wins when several cases of one
# Check fail differently): ``crashed`` > ``no-output`` > ``wrong-output``. The codes are symbolic;
# the learner-facing text is the Check's translated Feedback_Key and never contains expected output.
_RANK_PASS: Final = 0
_RANK_WRONG_OUTPUT: Final = 1
_RANK_NO_OUTPUT: Final = 2
_RANK_CRASHED: Final = 3
_FAILURE_CODES: Final = {
    _RANK_WRONG_OUTPUT: "wrong-output",
    _RANK_NO_OUTPUT: "no-output",
    _RANK_CRASHED: "crashed",
}


def _failure_rank(exit_status: int, stdout: bytes, want: ExpectedCase) -> int:
    """Classify one completed case (it neither timed out nor died on a signal).

    * ``crashed``: the program exited with a non-zero status that is not the expected one (an
      uncaught exception exits 1), whatever it printed.
    * ``no-output``: it exited as expected but printed nothing although output was expected.
    * ``wrong-output``: any other difference in stdout or exit status.
    """

    if exit_status != 0 and exit_status != want.exit_status:
        return _RANK_CRASHED
    if exit_status == want.exit_status and stdout == want.stdout:
        return _RANK_PASS
    if exit_status == want.exit_status and not stdout and want.stdout:
        return _RANK_NO_OUTPUT
    return _RANK_WRONG_OUTPUT
