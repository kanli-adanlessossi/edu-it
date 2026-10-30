"""Property test for lab data retention (hands-on-labs task 6.16).

Feature: hands-on-labs, Property 24: Retention deletes only old ended lab data, idempotently

**Validates: Requirements 9.1, 9.2, 9.3, 9.4**
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_application.labs import PurgeLabData
from edu_it_domain import config
from edu_it_domain.labs.models import (
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
    TaskCompletion,
)
from edu_it_domain.learning.models import Mastery, MasteryEvent, MasteryState
from edu_it_infrastructure.fakes import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_TERMINAL = {
    SessionStatus.ENDED,
    SessionStatus.EXPIRED,
    SessionStatus.IDLE_EXPIRED,
    SessionStatus.FAILED,
}

# (status, age of ended_at relative to the retention boundary in seconds, or None,
# number of runs). A zero offset sits exactly on the boundary (kept: strictly older only).
_row = st.tuples(
    st.sampled_from(list(SessionStatus)),
    st.one_of(st.none(), st.integers(min_value=-3 * 86400, max_value=3 * 86400)),
    st.integers(min_value=0, max_value=3),
)


@settings(max_examples=100, deadline=None)
@given(rows=st.lists(_row, max_size=12), extra=st.integers(min_value=0, max_value=86400))
def test_retention_deletes_only_old_ended_data_idempotently(
    rows: list[tuple[SessionStatus, int | None, int]], extra: int
) -> None:
    # Feature: hands-on-labs, Property 24: Retention deletes only old ended lab data, idempotently
    clock = Test_Clock()
    clock.advance(timedelta(seconds=extra))
    now = clock.now()
    runs = In_Memory_GradingRunRepository()
    sessions = In_Memory_LabSessionRepository(runs)
    mastery = In_Memory_MasteryRepository()

    expected_gone: set[str] = set()
    kept: set[str] = set()
    for i, (status, offset, n_runs) in enumerate(rows):
        sid = f"s{i}"
        # Only terminal sessions have an end time.
        ended = (
            None
            if status not in _TERMINAL or offset is None
            else now - config.LAB_RETENTION_PERIOD - timedelta(seconds=offset)
        )
        sessions._sessions[sid] = LabSession(
            sid, f"u{i}", "lab", status, now, now + timedelta(hours=1), ended_at=ended
        )
        for j in range(n_runs):
            run = GradingRun(f"r{i}-{j}", sid, RunStatus.PASSED, now, now + timedelta(seconds=15))
            runs.add(run)
            runs.add_task_completions(
                run.stable_id, [TaskCompletion(f"u{i}", f"t{i}-{j}", run.stable_id, now, True)]
            )
        mastery.save(
            Mastery(
                f"u{i}",
                "concept",
                50,
                MasteryState.PRACTICED,
                (MasteryEvent(5, "lab", now),),
            )
        )
        is_old = ended is not None and ended < now - config.LAB_RETENTION_PERIOD
        (expected_gone if is_old else kept).add(sid)

    mastery_before = dict(mastery._by_user_concept)
    purge = PurgeLabData(sessions=sessions, clock=clock)

    assert purge.execute() == len(expected_gone)

    for i, (_, _, n_runs) in enumerate(rows):
        sid = f"s{i}"
        present = sid in kept
        assert (sessions.get(sid) is not None) is present
        for j in range(n_runs):
            assert (runs.get(f"r{i}-{j}") is not None) is present
            assert runs.first_pass_exists(f"u{i}", f"t{i}-{j}") is present
    assert mastery._by_user_concept == mastery_before

    snapshot = (dict(sessions._sessions), dict(runs._runs), dict(runs._completions))
    assert purge.execute() == 0
    assert (dict(sessions._sessions), dict(runs._runs), dict(runs._completions)) == snapshot
    assert mastery._by_user_concept == mastery_before
