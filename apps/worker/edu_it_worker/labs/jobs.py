"""Worker job handlers for the lab jobs (Req 6.1, 6.5, 8.1, 9.1).

Each handler adapts a ``TaskQueue`` payload to an application use case. Handlers log and
swallow unexpected errors: every job is recoverable by a later one (the sweep re-enqueues
provisioning, the reaper closes stale runs and destroys orphans), and an exception would
stop the queue's delivery loop.

:class:`SweepProvisioning` is the ``lab.sweep`` logic: it re-enqueues provisioning for
sessions stuck in ``provisioning`` that are due, including those whose original enqueue
failed (dedupe keys collapse it with a job that is in fact still pending).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from edu_it_application.labs import (
    DESTROY_JOB,
    GRADE_JOB,
    PROVISION_JOB,
    provision_dedupe_key,
)
from edu_it_domain.labs.models import SessionStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.labs import (
        DestroySandbox,
        GradeRun,
        ProvisionSandbox,
        PurgeLabData,
        ReapSessions,
    )
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_sessions import LabSessionRepository
    from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue

__all__ = [
    "DESTROY_JOB",
    "GRADE_JOB",
    "PROVISION_JOB",
    "REAP_JOB",
    "RETAIN_JOB",
    "SWEEP_JOB",
    "SweepProvisioning",
    "destroy_handler",
    "grade_handler",
    "provision_handler",
    "reap_handler",
    "retain_handler",
    "sweep_handler",
]

_LOG = logging.getLogger(__name__)

REAP_JOB: Final = "lab.reap"
RETAIN_JOB: Final = "lab.retain"
SWEEP_JOB: Final = "lab.sweep"


class SweepProvisioning:
    """Re-enqueue ``lab.provision`` for every ``provisioning`` session that is due."""

    def __init__(self, *, sessions: LabSessionRepository, queue: TaskQueue, clock: Clock) -> None:
        """Wire the ports the sweep uses."""
        self._sessions = sessions
        self._queue = queue
        self._clock = clock

    def execute(self) -> int:
        """Enqueue provisioning for due sessions; return how many enqueues succeeded."""
        now = self._clock.now()
        enqueued = 0
        for session in self._sessions.list_active():
            if session.status is not SessionStatus.PROVISIONING:
                continue
            if session.next_provision_at is not None and now < session.next_provision_at:
                continue  # retry backoff not elapsed yet
            try:
                self._queue.enqueue(
                    PROVISION_JOB,
                    {"session_id": session.stable_id},
                    dedupe_key=provision_dedupe_key(session.stable_id),
                )
            except Exception:  # one failure must not starve the other sessions
                _LOG.warning(
                    "sweep could not enqueue provisioning for session %s",
                    session.stable_id,
                    exc_info=True,
                )
                continue
            enqueued += 1
        return enqueued


def _guarded(name: str, body: Callable[[JobPayload], object]) -> JobHandler:
    def handler(payload: JobPayload) -> None:
        try:
            body(payload)
        except Exception:
            _LOG.warning("job %s failed; a later job recovers it", name, exc_info=True)

    return handler


def _required_str(payload: JobPayload, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"payload is missing {key!r}")
    return value


def provision_handler(use_case: ProvisionSandbox) -> JobHandler:
    """``lab.provision``: ``{"session_id"}``."""
    return _guarded(PROVISION_JOB, lambda p: use_case.execute(_required_str(p, "session_id")))


def grade_handler(use_case: GradeRun) -> JobHandler:
    """``lab.grade``: ``{"run_id", "session_id"}``."""
    return _guarded(GRADE_JOB, lambda p: use_case.execute(_required_str(p, "run_id")))


def destroy_handler(use_case: DestroySandbox) -> JobHandler:
    """``lab.destroy``: ``{"session_id"}``."""
    return _guarded(DESTROY_JOB, lambda p: use_case.execute(_required_str(p, "session_id")))


def reap_handler(use_case: ReapSessions) -> JobHandler:
    """``lab.reap``: periodic, no payload."""
    return _guarded(REAP_JOB, lambda _p: use_case.execute())


def retain_handler(use_case: PurgeLabData) -> JobHandler:
    """``lab.retain``: periodic, no payload."""
    return _guarded(RETAIN_JOB, lambda _p: use_case.execute())


def sweep_handler(use_case: SweepProvisioning) -> JobHandler:
    """``lab.sweep``: periodic, no payload."""
    return _guarded(SWEEP_JOB, lambda _p: use_case.execute())
