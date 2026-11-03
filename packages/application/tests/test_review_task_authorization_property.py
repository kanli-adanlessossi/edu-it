"""Property test for review task authorization (ai-content task 13.3).

**Property 28: Review tasks are scoped and authorized**

**Validates: Requirements 15.1, 15.8, 15.11, 15.12**

Application level: a reviewer's listing equals exactly the open tasks of their
track, and every action by a user whose scope does not match the task's track
(other track, no scope, not a reviewer, unknown user) raises
``ReviewerExpertiseRequiredError`` (mapped to the 403 ``reviewer-expertise-required``
problem by the API) with the variant, the revision and the task unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_application.ai import (
    ListReviewTasks,
    ReviewerExpertiseRequiredError,
    ReviewTaskActions,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind
from edu_it_domain.ports.role_assignment import ROLE_LEARNER, ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
_TRACKS = ("trk-a", "trk-b", "trk-c")
_ACTIONS = ("get", "approve", "reject", "request_changes", "edit")


@settings(max_examples=60, deadline=None)
@given(
    task_tracks=st.lists(st.sampled_from(_TRACKS), max_size=12),
    action=st.sampled_from(_ACTIONS),
    outsider=st.sampled_from(["scopeless", "learner", "ghost", "rev-b", "rev-c"]),
)
def test_listing_is_scoped_and_foreign_actions_are_denied_without_writes(
    task_tracks: list[str], action: str, outsider: str
) -> None:
    """Listing equals the reviewer's track tasks; a foreign actor changes nothing."""
    clock = Test_Clock(_T0)
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    roles = In_Memory_RoleAssignmentRepository()
    roles.set_role(RoleAssignment("rev-a", ROLE_REVIEWER, "trk-a"))
    roles.set_role(RoleAssignment("rev-b", ROLE_REVIEWER, "trk-b"))
    roles.set_role(RoleAssignment("rev-c", ROLE_REVIEWER, "trk-c"))
    roles.set_role(RoleAssignment("scopeless", ROLE_REVIEWER))
    roles.set_role(RoleAssignment("learner", ROLE_LEARNER))

    by_track: dict[str, list[str]] = {t: [] for t in _TRACKS}
    task_ids: list[tuple[str, str, str]] = []  # (task id, variant id, track)
    for i, track in enumerate(task_tracks):
        clock.advance(timedelta(seconds=1))
        name = f"v{i}"
        variants.add(NewVariant(name, f"q-{name}", track, VariantState.PENDING_REVIEW))
        variants.add_revision(NewRevision(name, "c1", '{"en": {}}', origin="ai"))
        task = tasks.add(
            NewReviewTask(
                f"task-{name}",
                TaskKind.NEW_VARIANT,
                TargetKind.VARIANT,
                name,
                track,
                revision_content_version="c1",
            )
        )
        by_track[track].append(task.stable_id)
        task_ids.append((task.stable_id, name, track))

    codec = CursorCodec(clock)
    lister = ListReviewTasks(tasks=tasks, roles=roles, codec=codec)
    for reviewer, track in (("rev-a", "trk-a"), ("rev-b", "trk-b"), ("rev-c", "trk-c")):
        listed = [t.stable_id for t in lister.execute(reviewer, limit=1000).items]
        assert listed == by_track[track]
    with pytest.raises(ReviewerExpertiseRequiredError):
        lister.execute(outsider if outsider in ("scopeless", "learner", "ghost") else "ghost")

    actions = ReviewTaskActions(
        tasks=tasks, variants=variants, roles=roles, clock=clock, ids=Sequential_IdGenerator("new")
    )
    own_track = {"rev-b": "trk-b", "rev-c": "trk-c"}.get(outsider)
    for task_id, name, track in task_ids:
        if track == own_track:
            continue  # the actor is in scope here; only foreign tasks are denied
        before = (tasks.get(task_id), variants.get(name), variants.get_revision(name, "c1"))
        with pytest.raises(ReviewerExpertiseRequiredError):
            if action == "get":
                actions.get(outsider, task_id)
            elif action == "edit":
                actions.edit(outsider, task_id, {"en": {"prompt": "x"}})
            elif action == "request_changes":
                actions.request_changes(outsider, task_id, "no")
            else:
                getattr(actions, action)(outsider, task_id)
        after = (tasks.get(task_id), variants.get(name), variants.get_revision(name, "c1"))
        assert after == before
        # No revision was added by a denied edit.
    assert [t.stable_id for t in lister.execute("rev-a", limit=1000).items] == by_track["trk-a"]
