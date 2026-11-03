"""Reviewer queue router under ``/v1`` (ai-content Req 15.2, 15.8, 15.10).

A thin HTTP boundary over ``ListReviewTasks`` and ``ReviewTaskActions``
(``api -> application -> domain``). Every route needs a signed-in user; the use
cases check that the user is a reviewer of the task's track before any read of
the variant or any write. Mutating routes pass through the existing CSRF gate
(:mod:`edu_it_api.csrf`). Typed errors are rendered by :mod:`edu_it_api.errors.ai`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from edu_it_domain.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from fastapi import APIRouter, Query, Request, Response, status

from edu_it_api.review_tasks_schemas import (
    DecisionRequest,
    EditRequest,
    RequestChangesRequest,
    ReviewTaskDetailResponse,
    ReviewTaskPageResponse,
    ReviewTaskResponse,
    RevisionResponse,
    VariantSummaryResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.ai import ListReviewTasks, ReviewTaskActions
    from edu_it_domain.ports.review_tasks import ReviewTask
    from edu_it_domain.ports.variants import Variant, VariantRevision

__all__ = ["ReviewTaskServices", "build_review_tasks_router"]

_Cursor = Annotated[str | None, Query(max_length=1024)]
_Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


@dataclass(frozen=True, slots=True)
class ReviewTaskServices:
    """The use cases and identity resolver the reviewer queue router needs."""

    current_user: Callable[[Request], str]
    list_tasks: ListReviewTasks
    actions: ReviewTaskActions


def _task_view(task: ReviewTask) -> ReviewTaskResponse:
    return ReviewTaskResponse(
        stable_id=task.stable_id,
        kind=task.kind.value,
        target_kind=task.target_kind.value,
        target_stable_id=task.target_stable_id,
        track_id=task.track_stable_id,
        state=task.state.value,
        created_at=task.created_at,
        revision_content_version=task.revision_content_version,
        reason=task.reason,
        comment=task.comment,
        reviewer_id=task.reviewer_stable_id,
        decided_at=task.decided_at,
    )


def _variant_view(variant: Variant) -> VariantSummaryResponse:
    return VariantSummaryResponse(
        stable_id=variant.stable_id,
        parent_question_id=variant.parent_question_stable_id,
        track_id=variant.track_stable_id,
        state=variant.state.value,
        approved_content_version=variant.approved_content_version,
    )


def _revision_view(revision: VariantRevision) -> RevisionResponse:
    return RevisionResponse(
        content_version=revision.content_version,
        state=revision.state.value,
        body=json.loads(revision.body_json),
        origin=revision.origin,
        model=revision.model,
        prompt_id=revision.prompt_id,
        prompt_version=revision.prompt_version,
        generation_date=revision.generation_date,
        reviewed_by=revision.reviewed_by,
        reviewed_at=revision.reviewed_at,
        findings=None if revision.findings_json is None else json.loads(revision.findings_json),
        created_at=revision.created_at,
    )


def build_review_tasks_router(services: ReviewTaskServices) -> APIRouter:
    """Build the reviewer queue router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["review-tasks"])
    user_of = services.current_user

    @router.get("/review-tasks", response_model=ReviewTaskPageResponse)
    def list_review_tasks(
        request: Request, cursor: _Cursor = None, limit: _Limit = DEFAULT_PAGE_SIZE
    ) -> ReviewTaskPageResponse:
        """Open tasks of the reviewer's track, oldest first, one keyset page at a time."""
        page = services.list_tasks.execute(user_of(request), cursor=cursor, limit=limit)
        return ReviewTaskPageResponse(
            items=[_task_view(t) for t in page.items], next_cursor=page.next_cursor
        )

    @router.get("/review-tasks/{task_id}", response_model=ReviewTaskDetailResponse)
    def get_review_task(task_id: str, request: Request) -> ReviewTaskDetailResponse:
        """The task with its variant (FR and EN), provenance and findings."""
        detail = services.actions.get(user_of(request), task_id)
        return ReviewTaskDetailResponse(
            task=_task_view(detail.task),
            variant=None if detail.variant is None else _variant_view(detail.variant),
            revision=None if detail.revision is None else _revision_view(detail.revision),
        )

    @router.post("/review-tasks/{task_id}/approve", response_model=ReviewTaskResponse)
    def approve(
        task_id: str, request: Request, body: DecisionRequest | None = None
    ) -> ReviewTaskResponse:
        """Approve the task; records ``reviewed_by`` and ``reviewed_at``."""
        comment = None if body is None else body.comment
        return _task_view(services.actions.approve(user_of(request), task_id, comment))

    @router.post("/review-tasks/{task_id}/reject", response_model=ReviewTaskResponse)
    def reject(
        task_id: str, request: Request, body: DecisionRequest | None = None
    ) -> ReviewTaskResponse:
        """Reject the task."""
        comment = None if body is None else body.comment
        return _task_view(services.actions.reject(user_of(request), task_id, comment))

    @router.post("/review-tasks/{task_id}/request-changes", response_model=ReviewTaskResponse)
    def request_changes(
        task_id: str, body: RequestChangesRequest, request: Request
    ) -> ReviewTaskResponse:
        """Send the task back with a required comment."""
        return _task_view(services.actions.request_changes(user_of(request), task_id, body.comment))

    @router.post(
        "/review-tasks/{task_id}/edit",
        response_model=ReviewTaskResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def edit(
        task_id: str, body: EditRequest, request: Request, response: Response
    ) -> ReviewTaskResponse:
        """Store the edited variant as a new revision and return the task opened for it."""
        new_task = services.actions.edit(user_of(request), task_id, body.body)
        response.headers["Location"] = f"/v1/review-tasks/{new_task.stable_id}"
        return _task_view(new_task)

    return router
