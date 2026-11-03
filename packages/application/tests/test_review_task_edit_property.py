"""Property test: reviewer edits never interrupt or bypass serving (ai-content task 14.5).

Feature: ai-content, Property 29: Reviewer edits never interrupt or bypass serving

**Validates: Requirements 15.7, 16.1, 16.2, 16.3, 16.4**
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from edu_it_application.ai import ReviewTaskActions
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind, TaskState
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

TRACK = "trk-a"
_BODY = st.dictionaries(
    st.sampled_from(["prompt", "explanation", "hint"]),
    st.text(min_size=1, max_size=12),
    min_size=1,
    max_size=3,
).map(lambda d: {"en": d})


@settings(max_examples=60, deadline=None)
@given(
    state=st.sampled_from([VariantState.APPROVED, VariantState.SERVED]),
    edits=st.lists(
        st.tuples(_BODY, st.sampled_from(["approve", "reject"])),
        min_size=1,
        max_size=4,
        unique_by=lambda e: json.dumps(e[0], sort_keys=True),
    ),
)
def test_reviewer_edits_never_interrupt_or_bypass_serving(
    state: VariantState, edits: list[tuple[dict[str, dict[str, str]], str]]
) -> None:
    """Edits add a PendingReview revision; serving changes only on approve, never on reject."""
    clock = Test_Clock(datetime(2025, 1, 1, tzinfo=UTC))
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    roles = In_Memory_RoleAssignmentRepository()
    roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, TRACK))
    actions = ReviewTaskActions(
        tasks=tasks, variants=variants, roles=roles, clock=clock, ids=Sequential_IdGenerator("new")
    )
    variants.add(NewVariant("v1", "q", TRACK, VariantState.PENDING_REVIEW))
    variants.add_revision(NewRevision("v1", "c0", '{"en": {}}', origin="ai"))
    initial = tasks.add(
        NewReviewTask(
            "t-initial",
            TaskKind.NEW_VARIANT,
            TargetKind.VARIANT,
            "v1",
            TRACK,
            revision_content_version="c0",
        )
    )
    actions.approve("rev", initial.stable_id)
    approved = variants.get("v1")
    assert approved is not None
    variants.save(replace(approved, state=state))
    served = "c0"
    baseline = variants.get_revision("v1", "c0")

    for n, (body, decision) in enumerate(edits):
        base = f"base{n}"
        variants.add_revision(NewRevision("v1", base, '{"en": {"x": "y"}}'))
        task = tasks.add(
            NewReviewTask(
                f"t{n}",
                TaskKind.EDITED_REVISION,
                TargetKind.VARIANT,
                "v1",
                TRACK,
                revision_content_version=base,
            )
        )
        new_task = actions.edit("rev", task.stable_id, body)
        version = new_task.revision_content_version
        assert version is not None
        assert version not in {served, base}
        revision = variants.get_revision("v1", version)
        assert revision is not None
        assert revision.state is RevisionState.PENDING_REVIEW  # never Approved by an edit
        assert new_task.state is TaskState.OPEN
        # Serving is uninterrupted: same state, same approved version, same served revision.
        variant = variants.get("v1")
        assert variant is not None
        assert (variant.state, variant.approved_content_version) == (state, served)
        assert [r.content_version for r in variants.list_servable("q")] == [served]

        if decision == "approve":
            actions.approve("rev", new_task.stable_id)
            served = version
        else:
            actions.reject("rev", new_task.stable_id)
        variant = variants.get("v1")
        assert variant is not None
        assert variant.state is state  # no state change either way
        assert [r.content_version for r in variants.list_servable("q")] == [served]

    # The originally approved revision record itself is never altered.
    assert variants.get_revision("v1", "c0") == baseline
