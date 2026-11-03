"""Property test: flagging is idempotent and a review decision settles it (ai-content task 21.3).

Feature: ai-content, Property 37: Flagging is idempotent and a review decision settles it

**Validates: Requirements 18.4, 18.7, 19.3, 19.4, 19.5**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from edu_it_application.ai import FlagItem, FlagOutcome, ReviewTaskActions
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.item_info import ItemInfo
from edu_it_domain.ports.review_tasks import TaskKind, TaskState
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant
from edu_it_infrastructure.fakes import (
    In_Memory_ItemInfoSource,
    In_Memory_ItemQualityRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

S = VariantState
TRACK = "trk-a"
PARENT = "q1"


@settings(max_examples=80, deadline=None)
@given(
    is_variant=st.booleans(),
    state=st.sampled_from([S.APPROVED, S.SERVED]),
    sibling=st.booleans(),
    kind=st.sampled_from(list(FlagReasonKind)),
    detail=st.text(max_size=12),
    repeats=st.integers(min_value=1, max_value=4),
    decision=st.sampled_from(["approve", "reject", "none"]),
)
def test_flagging_is_idempotent_and_a_review_decision_settles_it(
    is_variant: bool,
    state: VariantState,
    sibling: bool,
    kind: FlagReasonKind,
    detail: str,
    repeats: int,
    decision: str,
) -> None:
    """One task however often it is flagged; approve restores serving, reject keeps it out."""
    clock = Test_Clock(datetime(2025, 1, 1, tzinfo=UTC))
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    quality = In_Memory_ItemQualityRepository()
    items = In_Memory_ItemInfoSource()
    roles = In_Memory_RoleAssignmentRepository()
    roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, TRACK))
    ids = Sequential_IdGenerator("id")
    refills: list[str] = []
    flag_item = FlagItem(
        flags=quality,
        tasks=tasks,
        variants=variants,
        items=items,
        ids=ids,
        request_refill=refills.append,
    )
    actions = ReviewTaskActions(
        tasks=tasks, variants=variants, roles=roles, clock=clock, ids=ids, flags=quality
    )

    def add_variant(name: str) -> None:
        variants.add(NewVariant(name, PARENT, TRACK, S.PENDING_REVIEW))
        variants.add_revision(NewRevision(name, "c1", "{}", origin="ai"))
        current = variants.get(name)
        assert current is not None
        steps = (S.APPROVED,) if state is S.APPROVED else (S.APPROVED, S.SERVED)
        for step in steps:
            current = replace(current, state=step, approved_content_version="c1")
            variants.save(current)

    if is_variant:
        key = ItemKey(ItemKind.VARIANT, "var1")
        add_variant("var1")
        if sibling:
            add_variant("var2")
    else:
        key = ItemKey(ItemKind.QUESTION, PARENT)
        items.set(key, ItemInfo(TRACK, "v1"))

    reason = FlagReason(kind, detail)
    results = [flag_item.execute(key, reason) for _ in range(repeats)]

    # Idempotent: the first call flags, every later call changes nothing.
    assert results[0].outcome is FlagOutcome.FLAGGED
    assert all(r.outcome is FlagOutcome.ALREADY_FLAGGED for r in results[1:])
    (task,) = tasks.page([TRACK], None, 100).items
    assert task.kind is TaskKind.FLAGGED_ITEM
    assert task.state is TaskState.OPEN
    assert task.reason is not None
    assert task.reason.startswith(kind.value)
    assert quality.active_flags([key]) == {key}

    if is_variant:
        variant = variants.get("var1")
        assert variant is not None
        assert variant.state is S.WITHDRAWN
        assert refills == ([] if sibling else [PARENT])
    else:
        assert refills == []

    if decision == "none":
        return
    done = (
        actions.approve("rev", task.stable_id)
        if decision == "approve"
        else actions.reject("rev", task.stable_id)
    )
    assert done.state is (TaskState.APPROVED if decision == "approve" else TaskState.REJECTED)

    if decision == "approve":
        assert quality.active_flags([key]) == frozenset()
        if is_variant:
            variant = variants.get("var1")
            assert variant is not None
            assert variant.state is S.APPROVED
    else:
        # Rejected: still flagged, still out of serving, and no second task is ever opened.
        assert quality.active_flags([key]) == {key}
        if is_variant:
            variant = variants.get("var1")
            assert variant is not None
            assert variant.state is S.WITHDRAWN
        assert flag_item.execute(key, reason).outcome is FlagOutcome.ALREADY_FLAGGED
        assert tasks.page([TRACK], None, 100).items == ()
