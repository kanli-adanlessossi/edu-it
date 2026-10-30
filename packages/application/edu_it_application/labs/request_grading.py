"""The ``RequestGrading`` use case (Req 11.1, 11.8, 15.3, 17.4, 27.5, 27.7, 30.4-30.6).

Order: ownership (a non-owner is indistinguishable from a missing session), the
``Idempotency-Key`` reservation, ``grading_eligibility``, the compare-and-set
``try_accept_grading``, then the ``GradingRun`` with ``accepted_at = Clock.now()``
and ``deadline_at = accepted_at + LAB_GRADING_RUN_DEADLINE``, and finally the
``lab.grade`` job. A repeat key returns the stored run without a second run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.labs.models import GradingRun, RunStatus
from edu_it_domain.labs.session_rules import (
    Rejected,
    RejectionReason,
    grading_eligibility,
)
from edu_it_domain.ports.idempotency import ReservationStatus

from edu_it_application.labs.errors import GradingRejectedError, LabSessionNotFoundError
from edu_it_application.learning.errors import IdempotencyConflictError, RequestInProgressError

if TYPE_CHECKING:
    from datetime import datetime

    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.idempotency import IdempotencyStore
    from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
    from edu_it_domain.ports.task_queue import TaskQueue

__all__ = ["GRADE_JOB", "RequestGradingResult", "RequestGradingUseCase", "grade_dedupe_key"]

GRADE_JOB: Final = "lab.grade"
_OPERATION: Final = "request-grading"


def grade_dedupe_key(run_id: StableId) -> str:
    """Dedupe key of the grade job of one run."""
    return f"grade:{run_id}"


@dataclass(frozen=True, slots=True)
class RequestGradingResult:
    """An accepted (or replayed) grading run."""

    run: GradingRun
    replayed: bool = False
    """``True`` when an ``Idempotency-Key`` repeat returned the stored run."""


class RequestGradingUseCase:
    """Accept a grading request and schedule the Grading_Job."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        runs: GradingRunRepository,
        queue: TaskQueue,
        idempotency: IdempotencyStore,
        clock: Clock,
        ids: IdGenerator,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._runs = runs
        self._queue = queue
        self._idempotency = idempotency
        self._clock = clock
        self._ids = ids
        self._config = config

    def execute(
        self, user_id: StableId, session_id: StableId, idempotency_key: str
    ) -> RequestGradingResult:
        """Request grading of ``session_id`` at most once per ``idempotency_key``.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
            IdempotencyConflictError: the key was used for a different session.
            RequestInProgressError: the same request is being processed elsewhere.
            GradingRejectedError: not ready, already grading, expired, or throttled.
        """
        if self._sessions.get_for_owner(session_id, user_id) is None:
            raise LabSessionNotFoundError(session_id)

        key = f"{_OPERATION}:{user_id}:{idempotency_key}"
        fingerprint = hashlib.sha256(
            json.dumps({"session_id": session_id}, sort_keys=True).encode()
        ).hexdigest()
        reservation = self._idempotency.reserve(key, fingerprint)
        if reservation.status is ReservationStatus.REPLAY:
            stored = reservation.result
            assert isinstance(stored, RequestGradingResult)
            return replace(stored, replayed=True)
        if reservation.status is ReservationStatus.CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key was reused with a different request body"
            )
        if reservation.status is ReservationStatus.IN_FLIGHT:
            raise RequestInProgressError("an identical request is still being processed")

        try:
            result = RequestGradingResult(run=self._accept(user_id, session_id))
        except Exception:
            self._idempotency.release(key)
            raise
        self._idempotency.complete(key, result)
        return result

    def _accept(self, user_id: StableId, session_id: StableId) -> GradingRun:
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)

        now = self._clock.now()
        verdict = grading_eligibility(session, now, self._config)
        if isinstance(verdict, Rejected):
            raise GradingRejectedError(verdict.reason, verdict.retry_after)

        min_interval = self._config.LAB_GRADING_MIN_INTERVAL  # type: ignore[attr-defined]
        if not self._sessions.try_accept_grading(session_id, now=now, min_interval=min_interval):
            # Lost the compare-and-set to a concurrent request: report why.
            raise self._lost_race(user_id, session_id, now)

        run = GradingRun(
            stable_id=self._ids.new_id(),
            session_id=session_id,
            status=RunStatus.RUNNING,
            accepted_at=now,
            deadline_at=now + self._config.LAB_GRADING_RUN_DEADLINE,  # type: ignore[attr-defined]
        )
        self._runs.add(run)
        self._sessions.touch_activity(session_id, now)
        self._queue.enqueue(
            GRADE_JOB,
            {"run_id": run.stable_id, "session_id": session_id},
            dedupe_key=grade_dedupe_key(run.stable_id),
        )
        return run

    def _lost_race(
        self, user_id: StableId, session_id: StableId, now: datetime
    ) -> GradingRejectedError:
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is not None:
            verdict = grading_eligibility(session, now, self._config)
            if isinstance(verdict, Rejected):
                return GradingRejectedError(verdict.reason, verdict.retry_after)
        return GradingRejectedError(RejectionReason.IN_PROGRESS)
