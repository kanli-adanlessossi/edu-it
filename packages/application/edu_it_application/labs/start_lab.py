"""The ``StartLab`` and ``ResetLab`` use cases (Req 6.1, 7.1, 7.5, 7.7, 15.7, 17.4, 30.1-30.3).

``StartLab`` honors the ``Idempotency-Key`` through the existing store, then:
unknown lab, active-session conflict (same or different lab), provisioning rate
limit, atomic ``add_if_no_active`` (so concurrent starts create at most one
Active_Session), and finally enqueues ``lab.provision`` with dedupe key
``provision:<session_id>``.

``ResetLab`` is end then start for the same lab; completions are never touched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from edu_it_domain import config as domain_config
from edu_it_domain.labs.models import LabSession, SessionStatus, StableId
from edu_it_domain.labs.session_rules import provision_rate_decision
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.id_generator import IdGenerator
from edu_it_domain.ports.idempotency import IdempotencyStore, ReservationStatus
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_domain.ports.lab_sessions import ActiveSessionExists, LabSessionRepository
from edu_it_domain.ports.pagination import Keyset
from edu_it_domain.ports.task_queue import TaskQueue

from edu_it_application.labs.end_session import EndSessionUseCase
from edu_it_application.labs.errors import (
    ActiveSessionConflictError,
    LabNotFoundError,
    LabProvisionRateLimitedError,
    LabSessionNotFoundError,
)
from edu_it_application.learning.errors import IdempotencyConflictError, RequestInProgressError

__all__ = [
    "PROVISION_JOB",
    "ResetLabUseCase",
    "StartLabResult",
    "StartLabUseCase",
    "provision_dedupe_key",
]

PROVISION_JOB: Final = "lab.provision"
_OPERATION: Final = "start-lab"
_PAGE: Final = 100


def provision_dedupe_key(session_id: StableId) -> str:
    """Dedupe key of the provision job of one session (Req 6.1)."""
    return f"provision:{session_id}"


@dataclass(frozen=True, slots=True)
class StartLabResult:
    """A started (or replayed) session."""

    session: LabSession
    replayed: bool = False
    """``True`` when an ``Idempotency-Key`` repeat returned the stored result."""


class StartLabUseCase:
    """Create a Lab_Session and schedule its provisioning."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        catalog: LabCatalog,
        queue: TaskQueue,
        idempotency: IdempotencyStore,
        clock: Clock,
        ids: IdGenerator,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._catalog = catalog
        self._queue = queue
        self._idempotency = idempotency
        self._clock = clock
        self._ids = ids
        self._config = config

    def execute(self, user_id: StableId, lab_id: StableId, idempotency_key: str) -> StartLabResult:
        """Start ``lab_id`` for ``user_id`` at most once per ``idempotency_key``.

        Raises:
            IdempotencyConflictError: the key was used with a different lab.
            RequestInProgressError: the same request is being processed elsewhere.
            LabNotFoundError: unknown or archived lab.
            ActiveSessionConflictError: the learner already has an Active_Session.
            LabProvisionRateLimitedError: the provisioning rate limit is exceeded.
        """
        key = f"{_OPERATION}:{user_id}:{idempotency_key}"
        fingerprint = hashlib.sha256(
            json.dumps({"lab_id": lab_id}, sort_keys=True).encode()
        ).hexdigest()
        reservation = self._idempotency.reserve(key, fingerprint)
        if reservation.status is ReservationStatus.REPLAY:
            stored = reservation.result
            assert isinstance(stored, StartLabResult)
            return replace(stored, replayed=True)
        if reservation.status is ReservationStatus.CONFLICT:
            raise IdempotencyConflictError(
                "Idempotency-Key was reused with a different request body"
            )
        if reservation.status is ReservationStatus.IN_FLIGHT:
            raise RequestInProgressError("an identical request is still being processed")

        try:
            result = StartLabResult(session=self._start(user_id, lab_id))
        except Exception:
            self._idempotency.release(key)
            raise
        self._idempotency.complete(key, result)
        return result

    def _start(self, user_id: StableId, lab_id: StableId) -> LabSession:
        lab = self._catalog.get(lab_id)
        if lab is None or lab.archived:
            raise LabNotFoundError(lab_id)

        active = self._sessions.get_active_for_user(user_id)
        if active is not None:
            raise self._conflict(active, lab_id)

        now = self._clock.now()
        self._check_rate_limit(user_id, now)

        session = LabSession(
            stable_id=self._ids.new_id(),
            user_id=user_id,
            lab_id=lab_id,
            status=SessionStatus.PROVISIONING,
            created_at=now,
            ttl_at=now + self._config.LAB_SESSION_TTL,  # type: ignore[attr-defined]
        )
        try:
            self._sessions.add_if_no_active(session)
        except ActiveSessionExists:
            # Lost a concurrent start: report the winner, create nothing (Req 30.2).
            winner = self._sessions.get_active_for_user(user_id)
            if winner is None:  # the winner ended in between; the learner may simply retry
                raise
            raise self._conflict(winner, lab_id) from None

        self._queue.enqueue(
            PROVISION_JOB,
            {"session_id": session.stable_id},
            dedupe_key=provision_dedupe_key(session.stable_id),
        )
        return session

    @staticmethod
    def _conflict(active: LabSession, requested_lab_id: StableId) -> ActiveSessionConflictError:
        return ActiveSessionConflictError(
            active_session_id=active.stable_id,
            active_lab_id=active.lab_id,
            requested_lab_id=requested_lab_id,
        )

    def _check_rate_limit(self, user_id: StableId, now: datetime) -> None:
        window: timedelta = self._config.LAB_PROVISION_RATE_WINDOW  # type: ignore[attr-defined]
        limit: int = self._config.LAB_PROVISION_RATE_LIMIT  # type: ignore[attr-defined]
        since = now - window
        if self._sessions.count_started_since(user_id, since) < limit:
            return
        # Over the limit: gather the start instants in the window to compute Retry-After.
        starts: list[datetime] = []
        after: Keyset | None = None
        while True:
            page = self._sessions.list_for_user_page(user_id, after=after, limit=_PAGE)
            starts.extend(s.created_at for s in page if s.created_at >= since)
            if len(page) < _PAGE:
                break
            after = (page[-1].created_at, page[-1].stable_id)
        decision = provision_rate_decision(starts, now, self._config)
        if not decision.allowed:
            assert decision.retry_after is not None
            raise LabProvisionRateLimitedError(decision.retry_after)


class ResetLabUseCase:
    """End the learner's session and start a fresh one for the same lab (Req 15.7).

    Task completions are never touched: they live outside the session lifecycle.
    """

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        end: EndSessionUseCase,
        start: StartLabUseCase,
    ) -> None:
        """Wire the two use cases it composes."""
        self._sessions = sessions
        self._end = end
        self._start = start

    def execute(
        self, user_id: StableId, session_id: StableId, idempotency_key: str
    ) -> StartLabResult:
        """Reset ``session_id``: end it, then start the same lab again.

        Raises:
            LabSessionNotFoundError: absent or owned by another learner.
        """
        session = self._sessions.get_for_owner(session_id, user_id)
        if session is None:
            raise LabSessionNotFoundError(session_id)
        self._end.execute(user_id, session_id)
        return self._start.execute(user_id, session.lab_id, idempotency_key)
