"""RecordCheckResult applies lab mastery per Check (hands-on-labs 15.4; Req 19.1-19.6, 19.8)."""

from __future__ import annotations

from edu_it_application.labs import RecordCheckResult
from edu_it_domain import config
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.labs.progression import LAB_PASSED_SOURCE, LAB_SOURCE
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)

_LAB = LabDefinition(
    lab_id="lab1",
    kind="terminal",
    image="img",
    concept_ids=("cn1",),
    tasks=(LabTaskDefinition("t1", required=True), LabTaskDefinition("t2", required=True)),
    checks=(LabCheckDefinition("c1", "t1", 5.0), LabCheckDefinition("c2", "t2", 5.0)),
    bundle_path="lab1",
)


def _r(check: str, task: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(check, task, outcome, outcome.value, "fb")


def _env() -> tuple[
    RecordCheckResult, In_Memory_GradingRunRepository, In_Memory_MasteryRepository, LabSession
]:
    clock = Test_Clock()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    mastery = In_Memory_MasteryRepository()
    now = clock.now()
    session = LabSession(
        "s1", "u1", "lab1", SessionStatus.GRADING, now, now + config.LAB_SESSION_TTL
    )
    sessions.add_if_no_active(session)
    for run_id in ("r1", "r2"):
        runs.add(
            GradingRun(run_id, "s1", RunStatus.RUNNING, now, now + config.LAB_GRADING_RUN_DEADLINE)
        )
    return RecordCheckResult(runs=runs, mastery=mastery, clock=clock), runs, mastery, session


def test_first_pass_counts_once_and_repeat_pass_gains_nothing() -> None:
    record, runs, mastery, session = _env()
    record.execute("r1", session, _LAB, _r("c1", "t1", CheckOutcome.PASSED))
    after_first = mastery.get("u1", "cn1")
    assert after_first is not None and after_first.score == config.LAB_TASK_MASTERY_GAIN
    assert [e.source for e in after_first.events] == [LAB_SOURCE]

    # Another Check in the same run must not re-apply or duplicate t1.
    record.execute("r1", session, _LAB, _r("c2", "t2", CheckOutcome.FAILED))
    assert mastery.get("u1", "cn1") == after_first
    assert [(c.task_id, c.counted_for_mastery) for c in runs.list_task_completions("u1")] == [
        ("t1", True)
    ]

    # A later run passing t1 again inserts an uncounted row and gains nothing.
    record.execute("r2", session, _LAB, _r("c1", "t1", CheckOutcome.PASSED))
    assert mastery.get("u1", "cn1") == after_first
    rows = sorted(
        (c.run_id, c.task_id, c.counted_for_mastery) for c in runs.list_task_completions("u1")
    )
    assert rows == [("r1", "t1", True), ("r2", "t1", False)]


def test_failed_never_lowers_and_lab_passed_marker_on_last_required_task() -> None:
    record, _runs, mastery, session = _env()
    record.execute("r1", session, _LAB, _r("c1", "t1", CheckOutcome.PASSED))
    before = mastery.get("u1", "cn1")
    record.execute("r1", session, _LAB, _r("c2", "t2", CheckOutcome.COULD_NOT_COMPLETE))
    assert mastery.get("u1", "cn1") == before
    record.execute("r2", session, _LAB, _r("c2", "t2", CheckOutcome.PASSED))
    done = mastery.get("u1", "cn1")
    assert done is not None
    assert [e.source for e in done.events] == [LAB_SOURCE, LAB_SOURCE, LAB_PASSED_SOURCE]
    assert done.events[-1].delta == 0
