"""Lab progression and mastery wiring (hands-on-labs Req 19.1, 19.2, 19.4, 19.5, 19.8).

Pure function over a finished grading run. For each task whose Checks all
passed in the run:

- first pass for the learner: one counted ``TaskCompletion`` and one mastery
  event with source ``lab`` per covered concept, delta
  ``tempered_gain(LAB_TASK_MASTERY_GAIN, ...)`` (the hint multiplier applies
  only when the caller reports a reveal before the first pass);
- repeat pass: an uncounted completion and no mastery change.

Failed and could-not-complete outcomes change nothing, so mastery is never
lowered. When the last required task completes, a zero-delta ``lab_passed``
event is written per covered concept (design decision 9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from edu_it_domain.config import LAB_TASK_MASTERY_GAIN
from edu_it_domain.labs.models import CheckOutcome, TaskCompletion
from edu_it_domain.learning.hints import tempered_gain
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Mastery, MasteryState

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from edu_it_domain.labs.models import CheckResult, StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_catalog import LabDefinition

__all__ = [
    "LAB_PASSED_SOURCE",
    "LAB_SOURCE",
    "LabProgression",
    "apply_lab_results",
]

LAB_SOURCE = "lab"
LAB_PASSED_SOURCE = "lab_passed"


@dataclass(frozen=True, slots=True)
class LabProgression:
    """What to persist after a run: completions and the updated mastery values."""

    completions: tuple[TaskCompletion, ...] = ()
    mastery: tuple[Mastery, ...] = ()
    """Updated mastery, one per covered concept that changed, in lab order."""
    lab_passed: bool = False
    """True when this run completed the last required task of the lab."""


def _passed_tasks(lab: LabDefinition, results: Collection[CheckResult]) -> list[StableId]:
    """Task ids (lab order) whose every declared Check passed in ``results``."""

    outcome_by_check = {r.check_id: r.outcome for r in results}
    passed: list[StableId] = []
    for task in lab.tasks:
        check_ids = [c.check_id for c in lab.checks if c.task_id == task.task_id]
        if check_ids and all(outcome_by_check.get(cid) is CheckOutcome.PASSED for cid in check_ids):
            passed.append(task.task_id)
    return passed


def apply_lab_results(
    *,
    lab: LabDefinition,
    user_id: StableId,
    run_id: StableId,
    results: Collection[CheckResult],
    completed_task_ids: Collection[StableId],
    hinted_task_ids: Collection[StableId],
    mastery: Mapping[StableId, Mastery],
    clock: Clock,
) -> LabProgression:
    """Compute completions and mastery changes for one grading run.

    ``completed_task_ids`` are the tasks the learner already completed before
    this run; ``hinted_task_ids`` are tasks with a hint reveal before their
    first passing time; ``mastery`` holds the learner's current mastery by
    concept id (a missing concept starts at score 0).
    """

    already = set(completed_task_ids)
    hinted = set(hinted_task_ids)
    now = clock.now()
    current: dict[StableId, Mastery] = {}
    touched: set[StableId] = set()
    completions: list[TaskCompletion] = []

    def _current(concept_id: StableId) -> Mastery:
        found = current.get(concept_id) or mastery.get(concept_id)
        return found or Mastery(
            user_id=user_id,
            concept_id=concept_id,
            score=0,
            state=MasteryState.INTRODUCED,
        )

    def _apply(concept_id: StableId, delta: int, source: str) -> None:
        base = _current(concept_id)
        current[concept_id] = apply_delta(
            base,
            max(0, delta),
            clock=clock,
            source=source,
            review_passed=base.state is MasteryState.MASTERED,
        )
        touched.add(concept_id)

    for task_id in _passed_tasks(lab, results):
        first_pass = task_id not in already
        completions.append(
            TaskCompletion(
                user_id=user_id,
                task_id=task_id,
                run_id=run_id,
                completed_at=now,
                counted_for_mastery=first_pass,
            )
        )
        if not first_pass:
            continue
        gain = tempered_gain(
            LAB_TASK_MASTERY_GAIN, hint_revealed_before_submission=task_id in hinted
        )
        for concept_id in lab.concept_ids:
            _apply(concept_id, gain, LAB_SOURCE)
        already.add(task_id)

    required = {t.task_id for t in lab.tasks if t.required}
    newly_done = {c.task_id for c in completions if c.counted_for_mastery}
    lab_passed = bool(required) and required <= already and not required <= (already - newly_done)
    if lab_passed:
        for concept_id in lab.concept_ids:
            _apply(concept_id, 0, LAB_PASSED_SOURCE)

    updated = tuple(current[c] for c in lab.concept_ids if c in touched)
    return LabProgression(completions=tuple(completions), mastery=updated, lab_passed=lab_passed)
