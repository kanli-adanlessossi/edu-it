"""Problem report router under ``/v1`` (ai-content Req 18.2, 18.3, 18.5, 18.6).

A thin HTTP boundary over ``SubmitProblemReport``. The route needs a signed-in
learner, requires the ``Idempotency-Key`` header and passes through the existing CSRF
gate. Typed errors are rendered by :mod:`edu_it_api.errors.ai`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from edu_it_application.ai import SubmitProblemReportRequest
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from fastapi import APIRouter, Header, Request, Response, status

from edu_it_api.problem_reports_schemas import ProblemReportRequest, ProblemReportResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.ai import SubmitProblemReport

__all__ = ["ProblemReportServices", "build_problem_reports_router"]


@dataclass(frozen=True, slots=True)
class ProblemReportServices:
    """The use case and identity resolver the problem report router needs."""

    current_user: Callable[[Request], str]
    submit: SubmitProblemReport


def build_problem_reports_router(services: ProblemReportServices) -> APIRouter:
    """Build the problem report router for the ``/v1`` sub-application."""
    router = APIRouter(tags=["problem-reports"])

    @router.post(
        "/problem-reports",
        response_model=ProblemReportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def submit(
        body: ProblemReportRequest,
        request: Request,
        response: Response,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    ) -> ProblemReportResponse:
        """Store a report once per ``Idempotency-Key``; a repeat returns the original (200)."""
        result = services.submit.execute(
            SubmitProblemReportRequest(
                user_id=services.current_user(request),
                item=ItemKey(ItemKind(body.item_kind), body.item_id),
                content_version=body.content_version,
                category=body.category,
                idempotency_key=idempotency_key,
                comment=body.comment,
            )
        )
        if result.replayed:
            response.status_code = status.HTTP_200_OK
        r = result.report
        return ProblemReportResponse(
            stable_id=r.stable_id,
            item_kind=r.key.kind.value,
            item_id=r.key.stable_id,
            content_version=r.content_version,
            category=r.category,
            created_at=r.created_at,
            replayed=result.replayed,
        )

    return router
