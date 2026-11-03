"""Tests for ``ReviewTaskActions`` and ``ListReviewTasks`` (ai-content task 13.1).

**Validates: Requirements 15.1, 15.3, 15.4, 15.5, 15.6, 15.7, 15.10, 15.11, 15.12, 16.1, 16.2**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.ai import (
    InvalidReviewActionError,
    ListReviewTasks,
    ReviewerExpertiseRequiredError,
    ReviewTaskActions,
    ReviewTaskNotFoundError,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain.ai.lifecycle import InvalidTransitionError, VariantState
from edu_it_domain.ports.review_tasks import (
    NewReviewTask,
    ReviewTaskAlreadyDecidedError,
    TargetKind,
    TaskKind,
    TaskState,
)
from edu_it_domain.ports.role_assignment import ROLE_LEARNER, ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
S = VariantState
TRACK = "trk-a"


class _World:
    def __init__(self) -> None:
        self.clock = Test_Clock(_T0)
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.roles = In_Memory_RoleAssignmentRepository()
        self.roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, TRACK))
        self.roles.set_role(RoleAssignment("other", ROLE_REVIEWER, "trk-b"))
        self.roles.set_role(RoleAssignment("learner", ROLE_LEARNER))
        self.actions = ReviewTaskActions(
            tasks=self.tasks,
            variants=self.variants,
            roles=self.roles,
            clock=self.clock,
            ids=Sequential_IdGenerator("new"),
        )

    def pending(self, name: str = "v1", version: str = "c1") -> str:
        """A PendingReview variant with one PendingReview revision and an open task."""
        self.variants.add(NewVariant(name, "q", TRACK, S.PENDING_REVIEW))
        self.variants.add_revision(NewRevision(name, version, '{"en": {}}', origin="ai"))
        task = self.tasks.add(
            NewReviewTask(
                f"task-{name}",
                TaskKind.NEW_VARIANT,
                TargetKind.VARIANT,
                name,
                TRACK,
                revision_content_version=version,
            )
        )
        return task.stable_id


def test_approve_sets_variant_approved_and_records_reviewer() -> None:
    w = _World()
    task_id = w.pending()
    done = w.actions.approve("rev", task_id)
    variant = w.variants.get("v1")
    revision = w.variants.get_revision("v1", "c1")
    assert done.state is TaskState.APPROVED
    assert variant is not None
    assert (variant.state, variant.approved_content_version) == (S.APPROVED, "c1")
    assert revision is not None
    assert (revision.state, revision.reviewed_by, revision.reviewed_at) == (
        RevisionState.APPROVED,
        "rev",
        _T0,
    )


def test_reject_sets_variant_rejected() -> None:
    w = _World()
    task_id = w.pending()
    done = w.actions.reject("rev", task_id)
    variant = w.variants.get("v1")
    assert done.state is TaskState.REJECTED
    assert variant is not None
    assert variant.state is S.REJECTED
    revision = w.variants.get_revision("v1", "c1")
    assert revision is not None
    assert (revision.state, revision.reviewed_by) == (RevisionState.REJECTED, "rev")


def test_request_changes_keeps_pending_with_comment() -> None:
    w = _World()
    task_id = w.pending()
    done = w.actions.request_changes("rev", task_id, "  fix option B ")
    variant = w.variants.get("v1")
    assert done.state is TaskState.CHANGES_REQUESTED
    assert done.comment == "fix option B"
    assert variant is not None
    assert variant.state is S.PENDING_REVIEW


def test_request_changes_requires_a_comment() -> None:
    w = _World()
    task_id = w.pending()
    with pytest.raises(InvalidReviewActionError):
        w.actions.request_changes("rev", task_id, "   ")
    task = w.tasks.get(task_id)
    assert task is not None
    assert task.state is TaskState.OPEN


def test_edit_adds_pending_revision_and_requires_approval() -> None:
    w = _World()
    task_id = w.pending()
    new_task = w.actions.edit("rev", task_id, {"en": {"prompt": "edited"}})
    version = new_task.revision_content_version
    assert version is not None
    assert new_task.kind is TaskKind.EDITED_REVISION
    assert new_task.state is TaskState.OPEN
    edited = w.variants.get_revision("v1", version)
    assert edited is not None
    assert (edited.state, edited.reviewed_by, edited.origin) == (
        RevisionState.PENDING_REVIEW,
        "rev",
        "ai",
    )
    variant = w.variants.get("v1")
    assert variant is not None
    assert variant.state is S.PENDING_REVIEW
    assert variant.approved_content_version is None
    old = w.tasks.get(task_id)
    assert old is not None
    assert old.state is TaskState.CHANGES_REQUESTED
    # Approval of the edit approves the new version.
    w.actions.approve("rev", new_task.stable_id)
    variant = w.variants.get("v1")
    assert variant is not None
    assert (variant.state, variant.approved_content_version) == (S.APPROVED, version)


def test_edit_of_served_variant_keeps_serving_old_version() -> None:
    w = _World()
    task_id = w.pending()
    w.actions.approve("rev", task_id)
    served = w.variants.get("v1")
    assert served is not None
    w.variants.save(replace(served, state=S.SERVED))
    # An edit task on the live variant, created the way the refill/edit flow does.
    w.variants.add_revision(NewRevision("v1", "c2", '{"en": {"x": 1}}'))
    edit = w.tasks.add(
        NewReviewTask(
            "edit-1",
            TaskKind.EDITED_REVISION,
            TargetKind.VARIANT,
            "v1",
            TRACK,
            revision_content_version="c2",
        )
    )
    # Rejecting the edit leaves the approved revision served (Req 16.4).
    w.actions.reject("rev", edit.stable_id)
    assert [r.content_version for r in w.variants.list_servable("q")] == ["c1"]
    # A second edit, approved, moves serving to the new version without a state change.
    w.variants.add_revision(NewRevision("v1", "c3", '{"en": {"x": 2}}'))
    edit2 = w.tasks.add(
        NewReviewTask(
            "edit-2",
            TaskKind.EDITED_REVISION,
            TargetKind.VARIANT,
            "v1",
            TRACK,
            revision_content_version="c3",
        )
    )
    assert [r.content_version for r in w.variants.list_servable("q")] == ["c1"]
    w.actions.approve("rev", edit2.stable_id)
    variant = w.variants.get("v1")
    assert variant is not None
    assert variant.state is S.SERVED
    assert [r.content_version for r in w.variants.list_servable("q")] == ["c3"]


def test_edit_validates_body() -> None:
    w = _World()
    task_id = w.pending()
    with pytest.raises(InvalidReviewActionError):
        w.actions.edit("rev", task_id, {})
    with pytest.raises(InvalidReviewActionError):
        w.actions.edit("rev", task_id, "text")


@pytest.mark.parametrize("actor", ["other", "learner", "nobody"])
@pytest.mark.parametrize("action", ["approve", "reject", "request_changes", "edit"])
def test_denied_actor_changes_nothing(actor: str, action: str) -> None:
    w = _World()
    task_id = w.pending()
    before_task = w.tasks.get(task_id)
    before_variant = w.variants.get("v1")
    before_revision = w.variants.get_revision("v1", "c1")
    with pytest.raises(ReviewerExpertiseRequiredError):
        if action == "approve":
            w.actions.approve(actor, task_id)
        elif action == "reject":
            w.actions.reject(actor, task_id)
        elif action == "request_changes":
            w.actions.request_changes(actor, task_id, "x")
        else:
            w.actions.edit(actor, task_id, {"en": {}})
    assert w.tasks.get(task_id) == before_task
    assert w.variants.get("v1") == before_variant
    assert w.variants.get_revision("v1", "c1") == before_revision


def test_unknown_task_is_not_found() -> None:
    w = _World()
    with pytest.raises(ReviewTaskNotFoundError):
        w.actions.approve("rev", "missing")


def test_decided_task_cannot_be_decided_again() -> None:
    w = _World()
    task_id = w.pending()
    w.actions.reject("rev", task_id)
    with pytest.raises(ReviewTaskAlreadyDecidedError):
        w.actions.approve("rev", task_id)


def test_invalid_variant_move_writes_nothing() -> None:
    # A variant that is not PendingReview or servable cannot be approved.
    w = _World()
    w.variants.add(NewVariant("v9", "q", TRACK, S.FLAGGED))
    w.variants.add_revision(NewRevision("v9", "c1", "{}"))
    w.tasks.add(
        NewReviewTask(
            "t9",
            TaskKind.NEW_VARIANT,
            TargetKind.VARIANT,
            "v9",
            TRACK,
            revision_content_version="c1",
        )
    )
    with pytest.raises(InvalidTransitionError):
        w.actions.approve("rev", "t9")
    task = w.tasks.get("t9")
    assert task is not None
    assert task.state is TaskState.OPEN


def test_list_is_scoped_to_reviewer_track_and_paginates() -> None:
    w = _World()
    for i in range(5):
        w.clock.advance(timedelta(seconds=1))
        w.pending(f"v{i}")
    w.variants.add(NewVariant("vb", "q2", "trk-b", S.PENDING_REVIEW))
    w.tasks.add(NewReviewTask("tb", TaskKind.NEW_VARIANT, TargetKind.VARIANT, "vb", "trk-b"))
    lister = ListReviewTasks(tasks=w.tasks, roles=w.roles, codec=CursorCodec(w.clock))
    seen: list[str] = []
    cursor = None
    while True:
        page = lister.execute("rev", cursor=cursor, limit=2)
        seen.extend(t.stable_id for t in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [f"task-v{i}" for i in range(5)]
    with pytest.raises(ReviewerExpertiseRequiredError):
        lister.execute("learner")
