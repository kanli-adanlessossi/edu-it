"""``SubmitProblemReport`` (ai-content Req 18.2, 18.3, 18.5, 18.6; task 20.2).

Order: validate the category and the comment length (nothing is reserved or stored
on a rejection), reserve the ``Idempotency-Key``, then store the report once with the
item's ``stable_id`` and the ``content_version`` the learner saw. A repeat with the
same key and body returns the original result; a reused key with another body is a
conflict.

The comment is personal free text. It is stored and nothing else: this module imports
no LLM port and no ``LLMInput`` type, so the comment has no path to a model (Req 18.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

from edu_it_domain import config
from edu_it_domain.ports.idempotency import ReservationStatus
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_domain.ports.problem_reports import ProblemReport

from edu_it_application.learning.errors import IdempotencyConflictError, RequestInProgressError

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.idempotency import IdempotencyStore
    from edu_it_domain.ports.problem_reports import ProblemReportRepository

__all__ = [
    "InvalidProblemReportError",
    "SubmitProblemReport",
    "SubmitProblemReportRequest",
    "SubmitProblemReportResult",
    "decode_problem_report_result",
    "encode_problem_report_result",
]

_OPERATION = "problem-report"
_LOG = logging.getLogger(__name__)


class InvalidProblemReportError(Exception):
    """The report is rejected before anything is stored (422).

    ``reason`` is a stable machine code: ``comment_too_long`` or ``invalid_category``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid problem report: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SubmitProblemReportRequest:
    """One submission."""

    user_id: str
    item: ItemKey
    content_version: str
    category: str
    idempotency_key: str
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitProblemReportResult:
    """The stored report and whether it is a replay of an earlier submission."""

    report: ProblemReport
    replayed: bool = False


def encode_problem_report_result(result: object) -> str:
    """Serialize a result for the idempotency store."""
    assert isinstance(result, SubmitProblemReportResult)
    r = result.report
    return json.dumps(
        {
            "stable_id": r.stable_id,
            "user_id": r.user_id,
            "kind": r.key.kind.value,
            "item": r.key.stable_id,
            "content_version": r.content_version,
            "category": r.category,
            "created_at": r.created_at.isoformat(),
            "comment": r.comment,
        }
    )


def decode_problem_report_result(text: str) -> SubmitProblemReportResult:
    """Inverse of :func:`encode_problem_report_result`; the result is marked replayed."""
    d = json.loads(text)
    return SubmitProblemReportResult(
        ProblemReport(
            stable_id=d["stable_id"],
            user_id=d["user_id"],
            key=ItemKey(ItemKind(d["kind"]), d["item"]),
            content_version=d["content_version"],
            category=d["category"],
            created_at=datetime.fromisoformat(d["created_at"]),
            comment=d["comment"],
        ),
        replayed=True,
    )


class SubmitProblemReport:
    """Store a learner's problem report at most once per ``Idempotency-Key``."""

    def __init__(
        self,
        *,
        reports: ProblemReportRepository,
        idempotency: IdempotencyStore,
        clock: Clock,
        ids: IdGenerator,
        on_stored: Callable[[ItemKey], object] | None = None,
    ) -> None:
        """Wire the ports.

        ``on_stored`` is told the reported item after a new report is stored (the API passes
        the ``ai.quality.evaluate_item`` enqueue). It is best-effort: a failure is logged and
        never fails or undoes the report. A replay does not call it.
        """
        self._reports = reports
        self._idempotency = idempotency
        self._clock = clock
        self._ids = ids
        self._on_stored = on_stored

    def execute(self, request: SubmitProblemReportRequest) -> SubmitProblemReportResult:
        """Validate, reserve the key and store the report.

        Raises:
            InvalidProblemReportError: unknown category or an oversize comment.
            IdempotencyConflictError: the key was used for a different report.
            RequestInProgressError: the same request is being processed elsewhere.
        """
        if request.category not in config.PROBLEM_REPORT_CATEGORIES:
            raise InvalidProblemReportError("invalid_category")
        if (
            request.comment is not None
            and len(request.comment) > config.PROBLEM_REPORT_COMMENT_MAX_CHARS
        ):
            raise InvalidProblemReportError("comment_too_long")

        key = f"{_OPERATION}:{request.user_id}:{request.idempotency_key}"
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "kind": request.item.kind.value,
                    "item": request.item.stable_id,
                    "content_version": request.content_version,
                    "category": request.category,
                    "comment": request.comment,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        reservation = self._idempotency.reserve(key, fingerprint)
        if reservation.status is ReservationStatus.REPLAY:
            stored = reservation.result
            assert isinstance(stored, SubmitProblemReportResult)
            return replace(stored, replayed=True)
        if reservation.status is ReservationStatus.CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key was reused with a different request body"
            )
        if reservation.status is ReservationStatus.IN_FLIGHT:
            raise RequestInProgressError("the same request is already being processed")

        report = ProblemReport(
            stable_id=self._ids.new_id(),
            user_id=request.user_id,
            key=request.item,
            content_version=request.content_version,
            category=request.category,
            created_at=self._clock.now(),
            comment=request.comment,
        )
        try:
            self._reports.add(report)
        except Exception:
            self._idempotency.release(key)
            raise
        result = SubmitProblemReportResult(report)
        self._idempotency.complete(key, result)
        if self._on_stored is not None:
            try:
                self._on_stored(request.item)
            except Exception:
                _LOG.warning("evaluation hook failed for %s", request.item, exc_info=True)
        return result
