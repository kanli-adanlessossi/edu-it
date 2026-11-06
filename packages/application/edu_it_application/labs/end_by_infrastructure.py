"""The ``EndByInfrastructure`` use case (kubernetes-deployment Req 19.2-19.4, 19.7, 19.8).

Ends a Lab_Session whose Lab_Pod was lost. The transition is ``infra_loss_transition``
(pure); this use case persists it with a compare-and-set and counts the loss.

* The session row is the only thing written: the Active_Session slot is derived from the
  session status, so ending the session frees it, and attempts, mastery and completions
  are never touched (Req 19.2-19.4).
* The metric is incremented only by the caller that wins the compare-and-set, so it
  counts exactly once per session however often the loss is detected (Req 19.7).
* Only ``ready`` and ``grading`` sessions with a sandbox are ended: a ``provisioning``
  session has no learner-visible pod yet and its own retry logic owns it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from edu_it_domain.labs.k8s_rules import LearnerLabStats, infra_loss_transition
from edu_it_domain.labs.models import SessionStatus

if TYPE_CHECKING:
    from edu_it_domain.labs.models import StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_metrics import LabMetrics
    from edu_it_domain.ports.lab_sessions import LabSessionRepository

__all__ = ["EndByInfrastructure"]

_LOSS_STATUSES = frozenset({SessionStatus.READY, SessionStatus.GRADING})


class EndByInfrastructure:
    """Mark a session ``ended-by-infrastructure`` and count the loss once."""

    def __init__(
        self, *, sessions: LabSessionRepository, clock: Clock, metrics: LabMetrics
    ) -> None:
        """Wire the ports the use case orchestrates."""
        self._sessions = sessions
        self._clock = clock
        self._metrics = metrics

    def execute(self, session_id: StableId) -> bool:
        """End ``session_id``; ``True`` only when this call performed the transition.

        Absent, terminal, provisioning and already-ended sessions return ``False`` and
        change nothing, so repeated detection is harmless.
        """
        session = self._sessions.get(session_id)
        if session is None or session.status not in _LOSS_STATUSES or session.sandbox_ref is None:
            return False
        result = infra_loss_transition(
            session, LearnerLabStats(session.stable_id, 0, 0.0), self._clock.now()
        )
        if result.counter_delta == 0:
            return False
        won = self._sessions.transition(
            session_id,
            expected=_LOSS_STATUSES,
            to=SessionStatus.ENDED_BY_INFRASTRUCTURE,
            ended_at=result.session.ended_at,
        )
        if not won:  # another detector (or the learner) got there first
            return False
        for _ in range(result.counter_delta):
            self._metrics.inc_infrastructure_loss()
        return True
