"""Tests for ``FoldObservations``, ``EvaluateFlags``, ``FlagItem`` and the flagged-item review
decisions (ai-content task 21.1).

**Validates: Requirements 18.4, 18.7, 18.8, 18.9, 19.1, 19.2, 19.3, 19.4, 19.5, 20.2**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from edu_it_application.ai import (
    EvaluateFlags,
    FlagItem,
    FlagOutcome,
    FoldObservations,
    InvalidReviewActionError,
    ReviewTaskActions,
)
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.config import ITEM_STAT_MIN_SAMPLES, REPORT_THRESHOLD
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.item_info import ItemInfo
from edu_it_domain.ports.item_quality import NewObservation
from edu_it_domain.ports.problem_reports import ProblemReport
from edu_it_domain.ports.review_tasks import ReviewTask, TaskKind, TaskState
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant
from edu_it_infrastructure.fakes import (
    In_Memory_ItemInfoSource,
    In_Memory_ItemQualityRepository,
    In_Memory_ProblemReportRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
S = VariantState
TRACK = "trk-a"
QUESTION = ItemKey(ItemKind.QUESTION, "q1")
REASON = FlagReason(FlagReasonKind.REPORTS, "reports=3")


class _World:
    def __init__(self) -> None:
        self.clock = Test_Clock(_T0)
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.quality = In_Memory_ItemQualityRepository()
        self.reports = In_Memory_ProblemReportRepository()
        self.items = In_Memory_ItemInfoSource()
        self.items.set(QUESTION, ItemInfo(TRACK, "v1"))
        self.refills: list[str] = []
        self.roles = In_Memory_RoleAssignmentRepository()
        self.roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, TRACK))
        ids = Sequential_IdGenerator("id")
        self.flag_item = FlagItem(
            flags=self.quality,
            tasks=self.tasks,
            variants=self.variants,
            items=self.items,
            ids=ids,
            request_refill=self.refills.append,
        )
        self.evaluate = EvaluateFlags(
            quality=self.quality,
            reports=self.reports,
            items=self.items,
            flag_item=self.flag_item,
        )
        self.actions = ReviewTaskActions(
            tasks=self.tasks,
            variants=self.variants,
            roles=self.roles,
            clock=self.clock,
            ids=ids,
            flags=self.quality,
        )

    def variant(self, name: str, state: VariantState = S.SERVED, parent: str = "q1") -> ItemKey:
        self.variants.add(NewVariant(name, parent, TRACK, S.PENDING_REVIEW))
        self.variants.add_revision(NewRevision(name, "c1", "{}", origin="ai"))
        # Walk the legal path to ``state`` through the repository.
        current = self.variants.get(name)
        assert current is not None
        for step in {
            S.APPROVED: (S.APPROVED,),
            S.SERVED: (S.APPROVED, S.SERVED),
        }[state]:
            current = replace(current, state=step, approved_content_version="c1")
            self.variants.save(current)
        return ItemKey(ItemKind.VARIANT, name)

    def open_tasks(self) -> list[ReviewTask]:
        return list(self.tasks.page([TRACK], None, 100).items)

    def report(self, user: str, key: ItemKey = QUESTION, version: str = "v1") -> None:
        self.reports.add(
            ProblemReport(f"r-{user}-{version}", user, key, version, "wrong_answer", _T0)
        )


# -- FoldObservations -----------------------------------------------------------------------


def test_fold_updates_statistics_and_cursor() -> None:
    w = _World()
    for correct, elapsed in [(True, 4000), (False, 8000), (True, None)]:
        w.quality.record(NewObservation("u", QUESTION, correct, _T0, elapsed))
    result = FoldObservations(quality=w.quality, batch_size=2).execute()
    stat = w.quality.get(QUESTION)
    assert stat is not None
    assert (stat.samples, stat.correct_count, stat.time_samples) == (3, 2, 2)
    assert stat.avg_time_ms == 6000
    assert (result.folded, result.cursor, result.touched) == (3, 3, frozenset({QUESTION}))
    assert FoldObservations(quality=w.quality).execute().folded == 0


# -- FlagItem -------------------------------------------------------------------------------


def test_flag_question_opens_one_task_and_is_idempotent() -> None:
    w = _World()
    first = w.flag_item.execute(QUESTION, REASON)
    second = w.flag_item.execute(QUESTION, REASON)
    assert first.outcome is FlagOutcome.FLAGGED
    assert second.outcome is FlagOutcome.ALREADY_FLAGGED
    assert w.quality.active_flags([QUESTION]) == {QUESTION}
    (task,) = w.open_tasks()
    assert (task.kind, task.target_stable_id, task.track_stable_id) == (
        TaskKind.FLAGGED_ITEM,
        "q1",
        TRACK,
    )
    assert task.reason is not None
    assert "reports=3" in task.reason


@pytest.mark.parametrize("state", [S.APPROVED, S.SERVED])
def test_flag_variant_withdraws_it_and_requests_a_refill_when_the_pool_empties(
    state: VariantState,
) -> None:
    w = _World()
    key = w.variant("var1", state)
    result = w.flag_item.execute(key, REASON)
    variant = w.variants.get("var1")
    assert variant is not None
    assert variant.state is S.WITHDRAWN
    assert variant.withdrawn_reason is not None
    assert result.refill_parent == "q1"
    assert w.refills == ["q1"]
    assert w.flag_item.execute(key, REASON).outcome is FlagOutcome.ALREADY_FLAGGED
    assert w.refills == ["q1"]
    assert len(w.open_tasks()) == 1


def test_flag_variant_with_siblings_left_requests_no_refill() -> None:
    w = _World()
    w.variant("var2")
    key = w.variant("var1")
    w.flag_item.execute(key, REASON)
    assert w.refills == []


def test_flag_unknown_item_and_unservable_variant_do_nothing() -> None:
    w = _World()
    assert (
        w.flag_item.execute(ItemKey(ItemKind.LESSON, "nope"), REASON).outcome
        is FlagOutcome.UNKNOWN_ITEM
    )
    w.variants.add(NewVariant("pend", "q1", TRACK, S.PENDING_REVIEW))
    key = ItemKey(ItemKind.VARIANT, "pend")
    assert w.flag_item.execute(key, REASON).outcome is FlagOutcome.NOT_SERVABLE
    assert w.quality.active_flags([key]) == frozenset()
    assert w.open_tasks() == []


# -- EvaluateFlags --------------------------------------------------------------------------


def test_evaluate_flags_on_report_threshold_of_current_version_only() -> None:
    w = _World()
    for n in range(REPORT_THRESHOLD - 1):
        w.report(f"u{n}")
    w.report("old", version="v0")  # another version does not count
    assert w.evaluate.execute([QUESTION]) == ()
    w.report("last")
    (result,) = w.evaluate.execute([QUESTION])
    assert result.outcome is FlagOutcome.FLAGGED
    (task,) = w.open_tasks()
    assert task.reason is not None
    assert f"reports={REPORT_THRESHOLD}" in task.reason
    assert w.evaluate.execute([QUESTION]) == ()  # already flagged: nothing more


def test_evaluate_flags_on_statistical_anomaly() -> None:
    w = _World()
    for _ in range(ITEM_STAT_MIN_SAMPLES):
        w.quality.record(NewObservation("u", QUESTION, False, _T0, 5000))
    touched = FoldObservations(quality=w.quality).execute().touched
    (result,) = w.evaluate.execute(touched)
    assert result.outcome is FlagOutcome.FLAGGED
    (task,) = w.open_tasks()
    assert task.reason is not None
    assert task.reason.startswith("statistics")


# -- review decisions -----------------------------------------------------------------------


def test_approve_flagged_question_clears_the_flag() -> None:
    w = _World()
    w.flag_item.execute(QUESTION, REASON)
    (task,) = w.open_tasks()
    done = w.actions.approve("rev", task.stable_id)
    assert done.state is TaskState.APPROVED
    assert w.quality.active_flags([QUESTION]) == frozenset()


def test_reject_flagged_question_keeps_the_flag_and_opens_no_new_task() -> None:
    w = _World()
    w.flag_item.execute(QUESTION, REASON)
    (task,) = w.open_tasks()
    assert w.actions.reject("rev", task.stable_id).state is TaskState.REJECTED
    assert w.quality.active_flags([QUESTION]) == {QUESTION}
    assert w.flag_item.execute(QUESTION, REASON).outcome is FlagOutcome.ALREADY_FLAGGED
    assert w.open_tasks() == []


def test_approve_flagged_variant_reinstates_it_and_clears_the_flag() -> None:
    w = _World()
    key = w.variant("var1")
    w.flag_item.execute(key, REASON)
    (task,) = w.open_tasks()
    w.actions.approve("rev", task.stable_id)
    variant = w.variants.get("var1")
    assert variant is not None
    assert (variant.state, variant.approved_content_version, variant.withdrawn_reason) == (
        S.APPROVED,
        "c1",
        None,
    )
    assert w.quality.active_flags([key]) == frozenset()


def test_reject_flagged_variant_keeps_it_withdrawn_and_flagged() -> None:
    w = _World()
    key = w.variant("var1")
    w.flag_item.execute(key, REASON)
    (task,) = w.open_tasks()
    w.actions.reject("rev", task.stable_id)
    variant = w.variants.get("var1")
    assert variant is not None
    assert variant.state is S.WITHDRAWN
    assert w.quality.active_flags([key]) == {key}


def test_request_changes_cannot_close_a_flagged_item_task() -> None:
    w = _World()
    w.flag_item.execute(QUESTION, REASON)
    (task,) = w.open_tasks()
    with pytest.raises(InvalidReviewActionError):
        w.actions.request_changes("rev", task.stable_id, "please check")
    assert w.quality.active_flags([QUESTION]) == {QUESTION}
    assert len(w.open_tasks()) == 1
