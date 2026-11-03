"""In-memory ``VariantRepository`` and ``ReviewTaskRepository`` (ai-content task 12.2).

The two fakes share one lock so ``decide`` writes the task, the variant and the
revision as one step. They validate everything before the first write, so a
failure leaves the stores untouched. Unlike the SQL adapters they hold no catalog
or user table, so they cannot reject an unknown parent question, job or reviewer.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from edu_it_domain.ai.lifecycle import SERVABLE_STATES, InvalidTransitionError, can_transition
from edu_it_domain.ports.pagination import Page
from edu_it_domain.ports.review_tasks import (
    Decision,
    NewReviewTask,
    ReviewTask,
    ReviewTaskAlreadyDecidedError,
    TargetKind,
    TaskState,
)
from edu_it_domain.ports.variants import NewRevision, NewVariant, Variant, VariantRevision

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.pagination import Keyset

__all__ = ["In_Memory_ReviewTaskRepository", "In_Memory_VariantRepository"]


def _utc(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


class In_Memory_VariantRepository:
    """In-memory ``VariantRepository``."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._variants: dict[str, Variant] = {}
        self._revisions: dict[tuple[str, str], VariantRevision] = {}
        self.lock = threading.RLock()

    def add(self, variant: NewVariant) -> None:
        with self.lock:
            if variant.stable_id in self._variants:
                raise ValueError(f"variant exists: {variant.stable_id}")
            self._variants[variant.stable_id] = Variant(
                stable_id=variant.stable_id,
                parent_question_stable_id=variant.parent_question_stable_id,
                track_stable_id=variant.track_stable_id,
                state=variant.state,
                created_at=_utc(self._clock.now()),
                generation_job_stable_id=variant.generation_job_stable_id,
            )

    def get(self, stable_id: str) -> Variant | None:
        with self.lock:
            return self._variants.get(stable_id)

    def _check_save(self, variant: Variant) -> None:
        """Raise when ``variant`` cannot be stored; write nothing."""
        stored = self._variants.get(variant.stable_id)
        if stored is None:
            raise ValueError(f"unknown variant {variant.stable_id!r}")
        if variant.state != stored.state and not can_transition(stored.state, variant.state):
            raise InvalidTransitionError(stored.state, variant.state)

    def save(self, variant: Variant) -> None:
        with self.lock:
            self._check_save(variant)
            stored = self._variants[variant.stable_id]
            self._variants[variant.stable_id] = replace(
                stored,
                state=variant.state,
                approved_content_version=variant.approved_content_version,
                withdrawn_reason=variant.withdrawn_reason,
            )

    def _servable(self, parent: str) -> list[Variant]:
        return sorted(
            (
                v
                for v in self._variants.values()
                if v.parent_question_stable_id == parent and v.state in SERVABLE_STATES
            ),
            key=lambda v: v.stable_id,
        )

    def count_approved(self, parent_question_stable_id: str) -> int:
        with self.lock:
            return len(self._servable(parent_question_stable_id))

    def parents_below(self, threshold: int) -> Sequence[str]:
        with self.lock:
            parents = {v.parent_question_stable_id for v in self._variants.values()}
            return sorted(p for p in parents if len(self._servable(p)) < threshold)

    def list_servable(self, parent_question_stable_id: str) -> Sequence[VariantRevision]:
        with self.lock:
            found: list[VariantRevision] = []
            for variant in self._servable(parent_question_stable_id):
                if variant.approved_content_version is None:
                    continue
                revision = self._revisions.get(
                    (variant.stable_id, variant.approved_content_version)
                )
                if revision is not None:
                    found.append(revision)
            return found

    def add_revision(self, revision: NewRevision) -> None:
        with self.lock:
            if revision.variant_stable_id not in self._variants:
                raise ValueError(f"unknown variant {revision.variant_stable_id!r}")
            key = (revision.variant_stable_id, revision.content_version)
            if key in self._revisions:
                raise ValueError(f"revision exists: {key}")
            self._revisions[key] = VariantRevision(
                variant_stable_id=revision.variant_stable_id,
                content_version=revision.content_version,
                body_json=revision.body_json,
                state=revision.state,
                created_at=_utc(self._clock.now()),
                origin=revision.origin,
                model=revision.model,
                prompt_id=revision.prompt_id,
                prompt_version=revision.prompt_version,
                generation_date=revision.generation_date,
                reviewed_by=revision.reviewed_by,
                reviewed_at=revision.reviewed_at,
                findings_json=revision.findings_json,
            )

    def get_revision(self, variant_stable_id: str, content_version: str) -> VariantRevision | None:
        with self.lock:
            return self._revisions.get((variant_stable_id, content_version))


class In_Memory_ReviewTaskRepository:
    """In-memory ``ReviewTaskRepository`` writing through ``variants`` on ``decide``."""

    def __init__(self, clock: Clock, variants: In_Memory_VariantRepository) -> None:
        self._clock = clock
        self._variants = variants
        self._tasks: dict[str, ReviewTask] = {}

    def add(self, task: NewReviewTask) -> ReviewTask:
        with self._variants.lock:
            if task.stable_id in self._tasks:
                raise ValueError(f"review task exists: {task.stable_id}")
            for existing in self._tasks.values():
                if (
                    existing.state is TaskState.OPEN
                    and existing.kind == task.kind
                    and existing.target_kind == task.target_kind
                    and existing.target_stable_id == task.target_stable_id
                    and existing.revision_content_version == task.revision_content_version
                ):
                    return existing
            stored = ReviewTask(
                stable_id=task.stable_id,
                kind=task.kind,
                target_kind=task.target_kind,
                target_stable_id=task.target_stable_id,
                track_stable_id=task.track_stable_id,
                state=TaskState.OPEN,
                created_at=_utc(self._clock.now()),
                revision_content_version=task.revision_content_version,
                reason=task.reason,
            )
            self._tasks[task.stable_id] = stored
            return stored

    def get(self, stable_id: str) -> ReviewTask | None:
        with self._variants.lock:
            return self._tasks.get(stable_id)

    def decide(self, task: ReviewTask, decision: Decision) -> None:
        with self._variants.lock:
            stored = self._tasks.get(task.stable_id)
            if stored is None:
                raise ValueError(f"unknown review task {task.stable_id!r}")
            if stored.state is not TaskState.OPEN:
                raise ReviewTaskAlreadyDecidedError(task.stable_id)
            is_variant = stored.target_kind is TargetKind.VARIANT
            if decision.variant is not None:
                if not is_variant or decision.variant.stable_id != stored.target_stable_id:
                    raise ValueError("decision variant is not the task target")
                self._variants._check_save(decision.variant)
            revision_key: tuple[str, str] | None = None
            if decision.revision_state is not None:
                if not is_variant or stored.revision_content_version is None:
                    raise ValueError("task names no revision")
                revision_key = (stored.target_stable_id, stored.revision_content_version)
                if revision_key not in self._variants._revisions:
                    raise ValueError(f"unknown revision {revision_key}")
            # Everything is validated: apply the three writes.
            decided_at = _utc(decision.decided_at)
            if decision.variant is not None:
                self._variants.save(decision.variant)
            if decision.revision_state is not None and revision_key is not None:
                self._variants._revisions[revision_key] = replace(
                    self._variants._revisions[revision_key],
                    state=decision.revision_state,
                    reviewed_by=decision.reviewer_stable_id,
                    reviewed_at=decided_at,
                )
            self._tasks[task.stable_id] = replace(
                stored,
                state=decision.outcome.task_state,
                comment=decision.comment,
                reviewer_stable_id=decision.reviewer_stable_id,
                decided_at=decided_at,
            )

    def page(self, tracks: Collection[str], after: Keyset | None, limit: int) -> Page[ReviewTask]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        wanted = set(tracks)
        with self._variants.lock:
            ordered = sorted(
                (
                    t
                    for t in self._tasks.values()
                    if t.state is TaskState.OPEN and t.track_stable_id in wanted
                ),
                key=lambda t: (t.created_at, t.stable_id),
            )
        if after is not None:
            position = (_utc(after[0]), after[1])
            ordered = [t for t in ordered if (t.created_at, t.stable_id) > position]
        items = tuple(ordered[:limit])
        more = len(ordered) > limit
        next_after = (items[-1].created_at, items[-1].stable_id) if more else None
        return Page(items, next_after)
