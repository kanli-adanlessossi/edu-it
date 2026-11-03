"""Reviewer queue use cases (ai-content Req 15.1, 15.3 to 15.7, 15.10 to 15.12, 16.1, 16.2).

``ReviewTaskActions`` offers approve, reject, request changes and edit. Every
action checks the actor's reviewer track scope against the task's track first;
a denial raises before any write, so the variant, the revisions and the task
stay untouched (Req 15.8, 15.11, 15.12). Approve, reject and request changes
are one ``ReviewTaskRepository.decide`` call, which writes the task, the variant
and the revision in one transaction.

A reviewer edit never changes a servable variant: it adds a ``PendingReview``
revision under a new ``content_version`` and opens a task for it, so the
previously approved revision keeps being served until the edit is approved
(Req 16.1, 16.2). ``ListReviewTasks`` pages the open tasks of the reviewer's
track with the shared cursor codec.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from edu_it_domain.ai.lifecycle import SERVABLE_STATES, VariantState, transition
from edu_it_domain.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.review_tasks import (
    Decision,
    DecisionOutcome,
    NewReviewTask,
    ReviewTask,
    ReviewTaskAlreadyDecidedError,
    TargetKind,
    TaskKind,
    TaskState,
)
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER
from edu_it_domain.ports.variants import NewRevision, RevisionState

from edu_it_application.ai.errors import (
    InvalidReviewActionError,
    ReviewerExpertiseRequiredError,
    ReviewTaskNotFoundError,
)
from edu_it_application.learning.pagination import Page

if TYPE_CHECKING:
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.item_flags import ItemFlagWriter
    from edu_it_domain.ports.review_tasks import ReviewTaskRepository
    from edu_it_domain.ports.role_assignment import RoleAssignmentRepository
    from edu_it_domain.ports.variants import Variant, VariantRepository, VariantRevision

    from edu_it_application.learning.pagination import CursorCodec

__all__ = ["ListReviewTasks", "ReviewTaskActions", "ReviewTaskDetail", "revision_content_version"]

_VARIANT_TASK_KINDS = frozenset({TaskKind.NEW_VARIANT, TaskKind.EDITED_REVISION})


def revision_content_version(body: object) -> str:
    """SHA-256 of the canonical JSON of ``body`` (sorted keys, no whitespace)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reviewer_track(roles: RoleAssignmentRepository, actor: str) -> str:
    """Return the actor's track scope, or raise when the actor is not a scoped reviewer."""
    assignment = roles.get_for_user(actor)
    if assignment is None or assignment.role != ROLE_REVIEWER or not assignment.track_scope:
        raise ReviewerExpertiseRequiredError(actor)
    return assignment.track_scope


@dataclass(frozen=True, slots=True)
class ReviewTaskDetail:
    """A task with the variant and revision a reviewer needs to decide (Req 15.2)."""

    task: ReviewTask
    variant: Variant | None
    revision: VariantRevision | None


class ReviewTaskActions:
    """Approve, reject, request changes and edit a review task."""

    def __init__(
        self,
        *,
        tasks: ReviewTaskRepository,
        variants: VariantRepository,
        roles: RoleAssignmentRepository,
        clock: Clock,
        ids: IdGenerator,
        flags: ItemFlagWriter | None = None,
    ) -> None:
        """Wire the ports. `flags` is needed to settle Review_Tasks of flagged items."""
        self._tasks = tasks
        self._variants = variants
        self._roles = roles
        self._clock = clock
        self._ids = ids
        self._flags = flags

    # -- reads ------------------------------------------------------------------------------

    def get(self, actor: str, task_id: str) -> ReviewTaskDetail:
        """Return the task with its variant and revision, after the scope check."""
        task = self._authorized_task(actor, task_id)
        variant = revision = None
        if task.target_kind is TargetKind.VARIANT:
            variant = self._variants.get(task.target_stable_id)
            if task.revision_content_version is not None:
                revision = self._variants.get_revision(
                    task.target_stable_id, task.revision_content_version
                )
        return ReviewTaskDetail(task, variant, revision)

    # -- actions ----------------------------------------------------------------------------

    def approve(self, actor: str, task_id: str, comment: str | None = None) -> ReviewTask:
        """Approve the task: the variant becomes Approved and serves the reviewed revision."""
        task = self._authorized_open_task(actor, task_id)
        if task.kind is TaskKind.FLAGGED_ITEM:
            return self._settle_flagged(task, DecisionOutcome.APPROVE, actor, comment)
        variant: Variant | None = None
        revision_state: RevisionState | None = None
        if self._has_variant_writes(task):
            current = self._require_variant(task)
            revision_state = RevisionState.APPROVED
            version = self._require_revision_version(task)
            if current.state in SERVABLE_STATES:
                variant = replace(current, approved_content_version=version)
            else:
                variant = replace(
                    current,
                    state=transition(current.state, VariantState.APPROVED),
                    approved_content_version=version,
                    withdrawn_reason=None,
                )
        return self._decide(task, DecisionOutcome.APPROVE, actor, comment, variant, revision_state)

    def reject(self, actor: str, task_id: str, comment: str | None = None) -> ReviewTask:
        """Reject the task. A servable variant is left alone, so its approved revision stays."""
        task = self._authorized_open_task(actor, task_id)
        if task.kind is TaskKind.FLAGGED_ITEM:
            return self._settle_flagged(task, DecisionOutcome.REJECT, actor, comment)
        variant: Variant | None = None
        revision_state: RevisionState | None = None
        if self._has_variant_writes(task):
            current = self._require_variant(task)
            self._require_revision_version(task)
            revision_state = RevisionState.REJECTED
            if current.state not in SERVABLE_STATES:
                variant = replace(current, state=transition(current.state, VariantState.REJECTED))
        return self._decide(task, DecisionOutcome.REJECT, actor, comment, variant, revision_state)

    def request_changes(self, actor: str, task_id: str, comment: str) -> ReviewTask:
        """Return the variant to PendingReview with the reviewer's comment attached."""
        task = self._authorized_open_task(actor, task_id)
        if task.kind is TaskKind.FLAGGED_ITEM:
            # Closing the only task would leave the flag with no decision to clear it.
            raise InvalidReviewActionError("unsupported_task")
        text = comment.strip() if comment else ""
        if not text:
            raise InvalidReviewActionError("comment_required")
        # The variant is already PendingReview (the lifecycle self loop): nothing to write.
        return self._decide(task, DecisionOutcome.REQUEST_CHANGES, actor, text, None, None)

    def edit(self, actor: str, task_id: str, body: object) -> ReviewTask:
        """Store ``body`` as a new PendingReview revision and open a task for it.

        The task being edited is closed as changes requested (superseded) and its
        revision is rejected. The variant state and ``approved_content_version``
        are not touched, so a served revision keeps being served (Req 16.1).
        Returns the new task.
        """
        task = self._authorized_open_task(actor, task_id)
        if not self._has_variant_writes(task):
            raise InvalidReviewActionError("unsupported_task")
        if not isinstance(body, dict) or not body:
            raise InvalidReviewActionError("body_invalid")
        self._require_variant(task)
        base_version = self._require_revision_version(task)
        base = self._variants.get_revision(task.target_stable_id, base_version)
        if base is None:
            raise InvalidReviewActionError("unknown_revision")
        version = revision_content_version(body)
        if self._variants.get_revision(task.target_stable_id, version) is not None:
            raise InvalidReviewActionError("unchanged_body")
        now = self._clock.now()
        self._variants.add_revision(
            NewRevision(
                variant_stable_id=task.target_stable_id,
                content_version=version,
                body_json=json.dumps(body, sort_keys=True, ensure_ascii=False),
                state=RevisionState.PENDING_REVIEW,
                origin=base.origin,
                model=base.model,
                prompt_id=base.prompt_id,
                prompt_version=base.prompt_version,
                generation_date=base.generation_date,
                reviewed_by=actor,
                reviewed_at=now,
                findings_json=base.findings_json,
            )
        )
        new_task = self._tasks.add(
            NewReviewTask(
                stable_id=self._ids.new_id(),
                kind=TaskKind.EDITED_REVISION,
                target_kind=TargetKind.VARIANT,
                target_stable_id=task.target_stable_id,
                track_stable_id=task.track_stable_id,
                revision_content_version=version,
                reason=task.reason,
            )
        )
        self._tasks.decide(
            task,
            Decision(
                DecisionOutcome.REQUEST_CHANGES,
                actor,
                now,
                comment=f"superseded by edit {version}",
                revision_state=RevisionState.REJECTED,
            ),
        )
        return new_task

    # -- helpers ----------------------------------------------------------------------------

    def _settle_flagged(
        self,
        task: ReviewTask,
        outcome: DecisionOutcome,
        actor: str,
        comment: str | None,
    ) -> ReviewTask:
        """Decide the Review_Task of a flagged item (Req 18.9, 19.4, 19.5).

        Approve returns the item to serving: a withdrawn variant is reinstated and the flag is
        cleared. Reject keeps the item out: the variant stays withdrawn and the flag stays
        active. The flag is cleared nowhere else. It is cleared after the decision is stored,
        so a crash in between leaves the item out of serving, never back in it unreviewed.
        """
        if self._flags is None:
            raise InvalidReviewActionError("unsupported_task")
        approving = outcome is DecisionOutcome.APPROVE
        variant: Variant | None = None
        if approving and task.target_kind is TargetKind.VARIANT:
            current = self._require_variant(task)
            if current.state not in SERVABLE_STATES:
                variant = replace(
                    current,
                    state=transition(current.state, VariantState.APPROVED),
                    withdrawn_reason=None,
                )
        decided = self._decide(task, outcome, actor, comment, variant, None)
        if approving:
            key = ItemKey(ItemKind(task.target_kind.value), task.target_stable_id)
            self._flags.clear_flag(key, task.stable_id)
        return decided

    def _authorized_task(self, actor: str, task_id: str) -> ReviewTask:
        track = _reviewer_track(self._roles, actor)
        task = self._tasks.get(task_id)
        if task is None:
            raise ReviewTaskNotFoundError(task_id)
        if task.track_stable_id != track:
            raise ReviewerExpertiseRequiredError(actor, task.track_stable_id)
        return task

    def _authorized_open_task(self, actor: str, task_id: str) -> ReviewTask:
        task = self._authorized_task(actor, task_id)
        if task.state is not TaskState.OPEN:
            raise ReviewTaskAlreadyDecidedError(task.stable_id)
        return task

    @staticmethod
    def _has_variant_writes(task: ReviewTask) -> bool:
        return task.target_kind is TargetKind.VARIANT and task.kind in _VARIANT_TASK_KINDS

    def _require_variant(self, task: ReviewTask) -> Variant:
        variant = self._variants.get(task.target_stable_id)
        if variant is None:
            raise InvalidReviewActionError("unsupported_task")
        return variant

    @staticmethod
    def _require_revision_version(task: ReviewTask) -> str:
        if task.revision_content_version is None:
            raise InvalidReviewActionError("unknown_revision")
        return task.revision_content_version

    def _decide(
        self,
        task: ReviewTask,
        outcome: DecisionOutcome,
        actor: str,
        comment: str | None,
        variant: Variant | None,
        revision_state: RevisionState | None,
    ) -> ReviewTask:
        self._tasks.decide(
            task,
            Decision(
                outcome,
                actor,
                self._clock.now(),
                comment=comment,
                variant=variant,
                revision_state=revision_state,
            ),
        )
        decided = self._tasks.get(task.stable_id)
        if decided is None:  # pragma: no cover - the task was just decided
            raise ReviewTaskNotFoundError(task.stable_id)
        return decided


class ListReviewTasks:
    """Cursor-paginated open review tasks of the reviewer's track."""

    def __init__(
        self,
        *,
        tasks: ReviewTaskRepository,
        roles: RoleAssignmentRepository,
        codec: CursorCodec,
    ) -> None:
        """Wire the ports."""
        self._tasks = tasks
        self._roles = roles
        self._codec = codec

    def execute(
        self, actor: str, *, cursor: str | None = None, limit: int | None = None
    ) -> Page[ReviewTask]:
        """Return the page after ``cursor`` (the first page when ``None``)."""
        track = _reviewer_track(self._roles, actor)
        scope = f"review-tasks:{track}"
        after = None if cursor is None else self._codec.decode(scope, cursor)
        size = DEFAULT_PAGE_SIZE if limit is None else max(1, min(limit, MAX_PAGE_SIZE))
        page = self._tasks.page([track], after, size)
        next_cursor = (
            None if page.next_after is None else self._codec.encode(scope, page.next_after)
        )
        return Page(items=page.items, next_cursor=next_cursor)
