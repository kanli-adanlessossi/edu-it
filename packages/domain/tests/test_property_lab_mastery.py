"""Property 12: lab results change mastery once, monotonically, tempered by hints.

Feature: hands-on-labs, Property 12: Lab results change mastery once, monotonically,
and tempered by hints

**Validates: Requirements 11.4, 19.1, 19.2, 19.4, 19.5, 19.6, 19.8, 27.6**
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_domain import config
from edu_it_domain.labs.models import CheckOutcome, CheckResult
from edu_it_domain.labs.progression import apply_lab_results
from edu_it_domain.learning.hints import tempered_gain
from edu_it_domain.learning.models import Mastery, MasteryState
from edu_it_domain.ports.lab_catalog import (
    LabCheckDefinition,
    LabDefinition,
    LabTaskDefinition,
)
from hypothesis import given, settings
from hypothesis import strategies as st

TASKS = ("t1", "t2", "t3")
CONCEPTS = ("c1", "c2")
OUTCOMES = (CheckOutcome.PASSED, CheckOutcome.FAILED, CheckOutcome.COULD_NOT_COMPLETE)


class _Clock:
    def now(self) -> datetime:
        return datetime(2025, 1, 1, tzinfo=UTC)


LAB = LabDefinition(
    lab_id="lab1",
    kind="terminal",
    image="img",
    concept_ids=CONCEPTS,
    tasks=(
        LabTaskDefinition("t1", required=True),
        LabTaskDefinition("t2", required=True),
        LabTaskDefinition("t3", required=False),
    ),
    checks=(
        LabCheckDefinition("k1", "t1", 5),
        LabCheckDefinition("k2", "t2", 5),
        LabCheckDefinition("k3", "t3", 5),
    ),
)

# One run: an outcome per task, plus the tasks whose hint was revealed before this run.
_run_strategy = st.tuples(
    st.lists(st.sampled_from(OUTCOMES), min_size=3, max_size=3),
    st.sets(st.sampled_from(TASKS)),
)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(_run_strategy, min_size=1, max_size=6),
    st.integers(min_value=0, max_value=60),
)
def test_lab_results_change_mastery_once_monotonically_tempered(
    runs: list[tuple[list[CheckOutcome], set[str]]], start_score: int
) -> None:
    # Feature: hands-on-labs, Property 12: Lab results change mastery once,
    # monotonically, and tempered by hints
    mastery: dict[str, Mastery] = {
        c: Mastery("u", c, start_score, MasteryState.PRACTICED) for c in CONCEPTS
    }
    start_events = {c: len(mastery[c].events) for c in CONCEPTS}
    completed: set[str] = set()
    ever_completed_rows: dict[str, int] = {}
    hinted: set[str] = set()
    hinted_at_first_pass: dict[str, bool] = {}
    unlocked = False

    for i, (outcomes, new_hints) in enumerate(runs):
        hinted |= new_hints
        results = [
            CheckResult(f"k{n + 1}", f"t{n + 1}", o, "code", "fb") for n, o in enumerate(outcomes)
        ]
        before = dict(mastery)
        out = apply_lab_results(
            lab=LAB,
            user_id="u",
            run_id=f"r{i}",
            results=results,
            completed_task_ids=tuple(completed),
            hinted_task_ids=tuple(hinted - completed),
            mastery=mastery,
            clock=_Clock(),
        )
        for m in out.mastery:
            mastery[m.concept_id] = m

        passed_now = {f"t{n + 1}" for n, o in enumerate(outcomes) if o is CheckOutcome.PASSED}
        # Failed / could-not-complete tasks produce no completion.
        assert {c.task_id for c in out.completions} == passed_now
        counted = {c.task_id for c in out.completions if c.counted_for_mastery}
        assert counted == passed_now - completed  # exactly once per task
        for t in (t for t in TASKS if t in counted):  # lab order within a run
            hinted_at_first_pass[t] = t in hinted
            ever_completed_rows[t] = ever_completed_rows.get(t, 0) + 1
        completed |= counted

        # Mastery never lowers; events never negative.
        for c in CONCEPTS:
            assert mastery[c].score >= before[c].score
            assert all(e.delta >= 0 for e in mastery[c].events)
        if not counted:
            assert out.mastery == ()

        # Unlock via Practiced is monotonic: once reached, later results never lower it.
        if all(m.score >= config.MASTERY_THRESHOLDS.practiced for m in mastery.values()):
            unlocked = True
        if unlocked:
            assert all(m.score >= config.MASTERY_THRESHOLDS.practiced for m in mastery.values())

    assert all(n == 1 for n in ever_completed_rows.values())

    # Replay expected lab events per concept: one per first-passed task, tempered by hints.
    first_pass_order = list(hinted_at_first_pass)
    for c in CONCEPTS:
        lab_events = [e for e in mastery[c].events[start_events[c] :] if e.source == "lab"]
        assert len(lab_events) == len(first_pass_order)
        score = start_score
        for e, t in zip(lab_events, first_pass_order, strict=True):
            gain = tempered_gain(
                config.LAB_TASK_MASTERY_GAIN,
                hint_revealed_before_submission=hinted_at_first_pass[t],
            )
            expected = min(gain, config.MASTERY_MAX - score)
            assert e.delta == expected
            score += expected
        assert mastery[c].score == score
