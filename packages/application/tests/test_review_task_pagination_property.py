"""Property test for review task pagination (ai-content task 13.4).

**Property 30: Review task pages cover the list exactly once**

**Validates: Requirements 15.10**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_application.ai import ListReviewTasks
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewVariant
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


@settings(max_examples=60, deadline=None)
@given(
    # Seconds to advance before each task; 0 produces equal timestamps (ties).
    steps=st.lists(st.integers(min_value=0, max_value=2), max_size=25),
    other_track_count=st.integers(min_value=0, max_value=4),
    limit=st.integers(min_value=1, max_value=8),
)
def test_pages_cover_the_list_exactly_once(
    steps: list[int], other_track_count: int, limit: int
) -> None:
    """Following ``next_cursor`` yields every task of the track once, in list order."""
    clock = Test_Clock(_T0)
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    roles = In_Memory_RoleAssignmentRepository()
    roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, "trk-a"))

    def add(name: str, track: str) -> tuple[datetime, str]:
        variants.add(NewVariant(name, f"q-{name}", track, VariantState.PENDING_REVIEW))
        task = tasks.add(
            NewReviewTask(f"task-{name}", TaskKind.NEW_VARIANT, TargetKind.VARIANT, name, track)
        )
        return task.created_at, task.stable_id

    created: list[tuple[datetime, str]] = []
    for i, step in enumerate(steps):
        clock.advance(timedelta(seconds=step))
        created.append(add(f"a{i}", "trk-a"))
    for j in range(other_track_count):
        clock.advance(timedelta(seconds=1))
        add(f"b{j}", "trk-b")
    # The documented list order is (created_at, stable_id): ties on created_at
    # break by stable_id (text order, so "task-a10" precedes "task-a2").
    expected = [stable_id for _, stable_id in sorted(created)]

    lister = ListReviewTasks(tasks=tasks, roles=roles, codec=CursorCodec(clock))
    # The unpaged list in one large page is the reference order.
    whole = lister.execute("rev", limit=1000)
    assert whole.next_cursor is None
    assert [t.stable_id for t in whole.items] == expected

    seen: list[str] = []
    cursor = None
    for _ in range(len(expected) + 2):
        page = lister.execute("rev", cursor=cursor, limit=limit)
        assert len(page.items) <= limit
        seen.extend(t.stable_id for t in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    else:  # pragma: no cover - pagination failed to terminate
        raise AssertionError("pagination did not terminate")

    assert seen == expected
    assert len(set(seen)) == len(seen)
