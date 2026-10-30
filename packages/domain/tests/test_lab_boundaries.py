"""Example boundary tests for the pure lab rules (hands-on-labs task 1.12).

**Validates: Requirements 7.4, 7.8, 7.9, 27.5, 27.7, 30.4, 1.7**
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from edu_it_domain import config
from edu_it_domain.labs import session_rules as rules
from edu_it_domain.labs.grading import UNSAFE_PATH_EXIT_CODE, map_exit_code
from edu_it_domain.labs.models import CheckOutcome, LabSession, SessionStatus
from edu_it_domain.labs.session_rules import (
    Eligible,
    ReapAction,
    Rejected,
    RejectionReason,
)

T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
EPS = timedelta(microseconds=1)
TEN_SECONDS = timedelta(seconds=10)


def _session(**kw: object) -> LabSession:
    base: dict[str, object] = {
        "stable_id": "s1",
        "user_id": "u1",
        "lab_id": "l1",
        "status": SessionStatus.READY,
        "created_at": T0,
        "ttl_at": T0 + config.LAB_SESSION_TTL,
    }
    base.update(kw)
    return LabSession(**base)  # type: ignore[arg-type]


# --- grading gap (Req 7.4, 30.4) -------------------------------------------------


def test_grading_gap_is_ten_seconds_in_config() -> None:
    assert config.LAB_GRADING_MIN_INTERVAL == TEN_SECONDS


def test_grading_at_exactly_ten_seconds_is_rejected() -> None:
    s = _session(last_grading_accepted_at=T0)
    result = rules.grading_eligibility(s, T0 + TEN_SECONDS, config)
    assert result == Rejected(RejectionReason.THROTTLED, retry_after=timedelta(0))


def test_grading_just_over_ten_seconds_is_eligible() -> None:
    s = _session(last_grading_accepted_at=T0)
    assert isinstance(rules.grading_eligibility(s, T0 + TEN_SECONDS + EPS, config), Eligible)


# --- grading request vs ttl_at (Req 7.8, 27.5, 27.7) -----------------------------


def test_grading_request_at_exactly_ttl_is_accepted() -> None:
    s = _session()
    assert isinstance(rules.grading_eligibility(s, s.ttl_at, config), Eligible)


def test_grading_request_strictly_after_ttl_is_rejected() -> None:
    s = _session()
    result = rules.grading_eligibility(s, s.ttl_at + EPS, config)
    assert result == Rejected(RejectionReason.EXPIRED)


# --- reap_decision (Req 7.8, 7.9) ------------------------------------------------


def test_reap_keeps_at_exactly_ttl_and_expires_just_after() -> None:
    # Fresh activity at ttl_at so the idle deadline cannot fire first.
    s = _session(last_activity_at=T0 + config.LAB_SESSION_TTL)
    assert rules.reap_decision(s, s.ttl_at, None, config) is ReapAction.KEEP
    assert rules.reap_decision(s, s.ttl_at + EPS, None, config) is ReapAction.EXPIRE


def test_reap_idle_deadline_boundary() -> None:
    s = _session(last_activity_at=T0)
    idle_at = T0 + config.LAB_IDLE_TIMEOUT
    assert rules.reap_decision(s, idle_at - EPS, None, config) is ReapAction.KEEP
    assert rules.reap_decision(s, idle_at, None, config) is ReapAction.IDLE_EXPIRE
    assert rules.reap_decision(s, idle_at + EPS, None, config) is ReapAction.IDLE_EXPIRE


# --- provisioning rate limit window (Req 1.7) ------------------------------------


def test_rate_limit_window_boundary() -> None:
    now = T0 + timedelta(hours=1)
    window = config.LAB_PROVISION_RATE_WINDOW
    limit = config.LAB_PROVISION_RATE_LIMIT
    # `limit` starts all strictly inside the window: denied.
    inside = [now - window + EPS] * limit
    assert not rules.provision_rate_decision(inside, now, config).allowed
    # One start exactly a window old no longer counts: allowed.
    edge = [now - window, *inside[1:]]
    assert rules.provision_rate_decision(edge, now, config).allowed


# --- exit-code mapping table (Req 11.2, 11.3, 29.9) ------------------------------


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    [
        (0, False, CheckOutcome.PASSED),
        (1, False, CheckOutcome.FAILED),
        (2, False, CheckOutcome.COULD_NOT_COMPLETE),
        (89, False, CheckOutcome.COULD_NOT_COMPLETE),
        (90, False, CheckOutcome.COULD_NOT_COMPLETE),
        (91, False, CheckOutcome.COULD_NOT_COMPLETE),
        (127, False, CheckOutcome.COULD_NOT_COMPLETE),
        (137, False, CheckOutcome.COULD_NOT_COMPLETE),
        (-9, False, CheckOutcome.COULD_NOT_COMPLETE),
        (None, False, CheckOutcome.COULD_NOT_COMPLETE),
        (0, True, CheckOutcome.COULD_NOT_COMPLETE),
        (1, True, CheckOutcome.COULD_NOT_COMPLETE),
        (None, True, CheckOutcome.COULD_NOT_COMPLETE),
    ],
)
def test_exit_code_mapping_table(
    exit_code: int | None, timed_out: bool, expected: CheckOutcome
) -> None:
    assert map_exit_code(None, exit_code, timed_out) is expected


def test_reserved_unsafe_path_code_is_90() -> None:
    assert UNSAFE_PATH_EXIT_CODE == 90


# --- no wall-clock reads in labs/ ------------------------------------------------


def test_labs_package_never_reads_the_wall_clock() -> None:
    labs_dir = Path(rules.__file__).parent
    pattern = re.compile(
        r"datetime\.(now|utcnow|today)\(|date\.today\(|time\.time\(|time\.monotonic\(|perf_counter"
    )
    for path in labs_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(("#", '"""'))
        )
        assert not pattern.search(code) or path.name == "__init__.py", path.name
