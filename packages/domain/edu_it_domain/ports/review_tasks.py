"""The ``ReviewTaskRepository`` port (ai-content Req 13.3, 15.4, 15.5, 15.10, 23.4).

A Review_Task asks a reviewer to decide on a variant, an edited revision or a
flagged item. ``decide`` records the decision and applies the variant and
revision changes it implies in one transaction, so a task is never decided
without its variant write, nor the reverse.

The reviewer's track scope is checked by the use case before any call here
(Req 15.8, 15.11, 15.12); this port performs no authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from edu_it_domain.ports.pagination import Keyset, Page
    from edu_it_domain.ports.variants import RevisionState, Variant

__all__ = [
    "Decision",
    "DecisionOutcome",
    "NewReviewTask",
    "ReviewTask",
    "ReviewTaskAlreadyDecidedError",
    "ReviewTaskRepository",
    "TargetKind",
    "TaskKind",
    "TaskState",
]


class TaskKind(StrEnum):
    """Why the task exists."""

    NEW_VARIANT = "new_variant"
    EDITED_REVISION = "edited_revision"
    FLAGGED_ITEM = "flagged_item"


class TargetKind(StrEnum):
    """What the task is about."""

    VARIANT = "variant"
    QUESTION = "question"
    LESSON = "lesson"
    LAB = "lab"


class TaskState(StrEnum):
    """``open`` until decided; every decision is final."""

    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


class DecisionOutcome(StrEnum):
    """What the reviewer decided."""

    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"

    @property
    def task_state(self) -> TaskState:
        """The state the task takes."""
        return {
            DecisionOutcome.APPROVE: TaskState.APPROVED,
            DecisionOutcome.REJECT: TaskState.REJECTED,
            DecisionOutcome.REQUEST_CHANGES: TaskState.CHANGES_REQUESTED,
        }[self]


class ReviewTaskAlreadyDecidedError(ValueError):
    """The task is no longer open (maps to 409 ``review-task-already-decided``)."""

    def __init__(self, stable_id: str) -> None:
        super().__init__(f"review task already decided: {stable_id}")
        self.stable_id = stable_id


@dataclass(frozen=True, slots=True)
class NewReviewTask:
    """Fields needed to open a task. ``created_at`` comes from the clock."""

    stable_id: str
    kind: TaskKind
    target_kind: TargetKind
    target_stable_id: str
    track_stable_id: str
    revision_content_version: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewTask:
    """A stored task."""

    stable_id: str
    kind: TaskKind
    target_kind: TargetKind
    target_stable_id: str
    track_stable_id: str
    state: TaskState
    created_at: datetime
    revision_content_version: str | None = None
    reason: str | None = None
    comment: str | None = None
    reviewer_stable_id: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """A reviewer decision and the writes it implies.

    ``variant`` is the variant as it must be stored (already moved through
    ``lifecycle.transition`` by the caller), or ``None`` when the variant is left
    alone (a reviewer edit of a served variant, Req 16). ``revision_state`` is the
    new state of the revision named by the task's ``revision_content_version``; the
    reviewer and ``decided_at`` are recorded on it as ``reviewed_by`` and
    ``reviewed_at``.
    """

    outcome: DecisionOutcome
    reviewer_stable_id: str
    decided_at: datetime
    comment: str | None = None
    variant: Variant | None = None
    revision_state: RevisionState | None = None


@runtime_checkable
class ReviewTaskRepository(Protocol):
    """Port storing review tasks."""

    def add(self, task: NewReviewTask) -> ReviewTask:
        """Open a task and return it.

        Idempotent per open target: when a task with the same kind, target and
        ``revision_content_version`` is still open, that task is returned and
        nothing is written. Raises ``ValueError`` for a duplicate ``stable_id``.
        """
        ...

    def get(self, stable_id: str) -> ReviewTask | None:
        """Return the task, or ``None`` when unknown."""
        ...

    def decide(self, task: ReviewTask, decision: Decision) -> None:
        """Record ``decision`` on the open task and apply its variant and revision
        writes, all in one transaction.

        Raises ``ReviewTaskAlreadyDecidedError`` when the task is not open,
        ``InvalidTransitionError`` when the variant move is not allowed, and
        ``ValueError`` for an unknown task, variant or revision, or a variant that is not the
        task target. The SQL adapter also rejects an unknown reviewer. In every error case
        nothing is written.
        """
        ...

    def page(self, tracks: Collection[str], after: Keyset | None, limit: int) -> Page[ReviewTask]:
        """Open tasks of ``tracks``, oldest first by ``(created_at, stable_id)``,
        strictly after ``after``. Ties on ``created_at`` break by ``stable_id`` in
        code-point order (Python ``str`` comparison, never a locale collation).
        ``limit`` must be at least 1."""
        ...
