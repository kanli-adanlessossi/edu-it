"""The ``ReapSessions`` and ``DestroySandbox`` worker job logic.

Requirements 6.5, 7.2, 7.3, 8.2-8.5, 10.8, 13.1, 25.5, 27.1, 27.2.

``ReapSessions`` (``lab.reap``) runs on every reaper tick and is idempotent:

1. Stale ``running`` runs (``now > deadline_at``) are thawed (``kill -s CONT -1`` as the
   learner, retried once) and closed as ``could-not-complete``; a run whose thaw still
   fails stays ``running`` and is retried on the next tick. A session left in
   ``grading`` goes back to ``ready``.
2. ``reap_decision`` is applied to every active session: ``EXPIRE`` / ``IDLE_EXPIRE``
   move it to a terminal status (expiry only when ``now > ttl_at``) and destroy its
   sandbox; ``DEFER`` leaves everything alone while a run is inside its deadline.
3. ``select_reaper_actions`` (pure: persisted rows plus the provider listing only, so a
   restarted worker resumes with no in-memory state) selects the pods to delete: every
   sandbox whose session is not active (terminal sessions, unknown sessions, failed
   provisions) is an orphan, unless a run of that session is still inside its deadline.
4. Active sessions whose pod is gone, failed or evicted are ended by infrastructure loss
   (``EndByInfrastructure``) and their failed or evicted pod object is removed in the
   same tick (kubernetes-deployment Req 6.6, 6.7, 19.2, 19.5, 20.3, 20.7).

Destroy failures are logged and not recorded anywhere: the sandbox is simply still
listed next tick and is destroyed then.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from edu_it_domain import config as domain_config
from edu_it_domain.labs.k8s_rules import select_reaper_actions
from edu_it_domain.labs.models import ACTIVE_SESSION_STATUSES, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import LEARNER_USER, WORKSPACE_PATH
from edu_it_domain.labs.session_rules import ReapAction, reap_decision
from edu_it_domain.ports.sandbox import SandboxGone

from edu_it_application.labs.save_files import sandbox_handle

if TYPE_CHECKING:
    from edu_it_domain.labs.models import GradingRun, LabSession, StableId
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.lab_metrics import LabMetrics
    from edu_it_domain.ports.lab_pods import LabPodWatcher
    from edu_it_domain.ports.lab_sessions import GradingRunRepository, LabSessionRepository
    from edu_it_domain.ports.sandbox import SandboxHandle, SandboxProvider

    from edu_it_application.labs.end_by_infrastructure import EndByInfrastructure

__all__ = ["DestroyOutcome", "DestroySandbox", "ReapReport", "ReapSessions"]

_LOG = logging.getLogger(__name__)

#: Absolute path and argv recorded in ``lab-images/README.md`` (task 3.4).
_THAW_ARGV: Final = ["/bin/kill", "-s", "CONT", "-1"]
_THAW_ATTEMPTS: Final = 2
_THAW_TIMEOUT_S: Final = 5.0

#: Empty ``/grader`` (a mount point) and verify it is empty, as root who owns the mount. The
#: same shell GradeRun._teardown runs; the reaper reuses it so a run interrupted by a Worker
#: restart has its grading bundle removed from the Lab_Pod (kubernetes-deployment Req 20.6).
_CLEAN_GRADER_ARGV: Final = [
    "/bin/bash",
    "-c",
    'shopt -s dotglob nullglob; /bin/rm -rf -- /grader/*; [ -z "$(/bin/ls -A /grader)" ]',
]
_CLEAN_GRADER_USER: Final = "root"
_CLEAN_GRADER_TIMEOUT_S: Final = 5.0


@dataclass(frozen=True, slots=True)
class ReapReport:
    """What one reaper tick did (for logs and tests)."""

    expired: int = 0
    idle_expired: int = 0
    deferred: int = 0
    destroyed: int = 0
    destroy_failures: int = 0
    runs_closed: int = 0
    thaw_failures: int = 0
    infra_lost: int = 0


def _destroy(sandbox: SandboxProvider, handle: SandboxHandle) -> bool:
    """Destroy ``handle``; log and return ``False`` on failure (retried next tick)."""
    try:
        sandbox.destroy(handle)
    except Exception:
        _LOG.warning("destroying sandbox %s failed; will retry", handle.sandbox_id, exc_info=True)
        return False
    return True


class ReapSessions:
    """Expire overdue sessions, close stale runs, and leave no sandbox behind."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        runs: GradingRunRepository,
        sandbox: SandboxProvider,
        clock: Clock,
        provider_name: str = "docker",
        config: object = domain_config,
        end_by_infrastructure: EndByInfrastructure | None = None,
        pod_watcher: LabPodWatcher | None = None,
        metrics: LabMetrics | None = None,
    ) -> None:
        """Wire the ports the job orchestrates.

        ``end_by_infrastructure`` enables infrastructure-loss detection, ``pod_watcher``
        supplies the failed or evicted pod ids, and ``metrics`` counts reaper deletions.
        """
        self._sessions = sessions
        self._runs = runs
        self._sandbox = sandbox
        self._clock = clock
        self._provider_name = provider_name
        self._config = config
        self._end = end_by_infrastructure
        self._watcher = pod_watcher
        self._metrics = metrics

    def execute(self) -> ReapReport:
        """Run one reaper tick."""
        now = self._clock.now()
        counts = dict.fromkeys(ReapReport.__slots__, 0)

        running = self._close_stale_runs(now, counts)

        for session in self._sessions.list_active():
            action = reap_decision(session, now, running.get(session.stable_id), self._config)
            if action is ReapAction.DEFER:
                counts["deferred"] += 1
            elif action in (ReapAction.EXPIRE, ReapAction.IDLE_EXPIRE):
                self._expire(session, action, now, counts)

        self._sweep_pods(now, running, counts)
        return ReapReport(**counts)

    # -- steps ---------------------------------------------------------------

    def close_interrupted_runs(self) -> int:
        """Close every ``running`` run as ``could-not-complete`` at Worker start.

        Called once before the Worker consumes a job (kubernetes-deployment Req 20.6): a run
        still ``running`` then was interrupted by the restart. It is thawed and closed like a
        stale run, never re-run, so a redelivered ``lab.grade`` job finds it finished and
        does nothing. Returns the number of runs closed.
        """
        counts = dict.fromkeys(ReapReport.__slots__, 0)
        self._close_stale_runs(self._clock.now(), counts, interrupted=True)
        return counts["runs_closed"]

    def _close_stale_runs(
        self, now: object, counts: dict[str, int], *, interrupted: bool = False
    ) -> dict[StableId, GradingRun]:
        """Close runs past their deadline; return the still-live running run per session.

        With ``interrupted`` every running run is closed, whatever its deadline.
        """
        live: dict[StableId, GradingRun] = {}
        for run in self._runs.list_running():
            if not interrupted and now <= run.deadline_at:  # type: ignore[operator]
                live[run.session_id] = run
                continue
            session = self._sessions.get(run.session_id)
            if session is not None and not self._thaw(session):
                counts["thaw_failures"] += 1
                continue  # stays running; thawed again next tick
            # Req 20.6: a run interrupted by a Worker restart leaves its grading bundle in the
            # Lab_Pod (GradeRun._teardown never ran). Remove it before closing the run, the same
            # way GradeRun does. A stale-deadline run already had its own teardown, so this only
            # applies to the interrupted path; it is idempotent either way.
            if interrupted and session is not None:
                self._clean_grader(session)
            if self._runs.finish(
                run.stable_id,
                status=RunStatus.COULD_NOT_COMPLETE,
                graded_at=now,  # type: ignore[arg-type]
            ):
                counts["runs_closed"] += 1
            if session is not None and session.status is SessionStatus.GRADING:
                self._sessions.transition(
                    session.stable_id,
                    expected={SessionStatus.GRADING},
                    to=SessionStatus.READY,
                )
        return live

    def _clean_grader(self, session: LabSession) -> None:
        """Remove the grading bundle from the Lab_Pod (Req 20.6); best effort and idempotent."""
        if session.sandbox_ref is None or not session.status.is_active:
            return  # no sandbox, or it is about to be destroyed
        handle = sandbox_handle(session.stable_id, session.sandbox_ref, self._provider_name)
        try:
            self._sandbox.exec(
                handle,
                _CLEAN_GRADER_ARGV,
                as_user=_CLEAN_GRADER_USER,
                env={},
                cwd=WORKSPACE_PATH,
                timeout_s=_CLEAN_GRADER_TIMEOUT_S,
            )
        except SandboxGone:
            return  # the pod is gone: nothing to clean
        except Exception:
            _LOG.warning("cleaning /grader of session %s failed", session.stable_id, exc_info=True)

    def _thaw(self, session: LabSession) -> bool:
        """Resume the learner's processes; ``True`` when done or nothing to thaw."""
        if session.sandbox_ref is None or not session.status.is_active:
            return True  # no sandbox, or it is about to be destroyed
        handle = sandbox_handle(session.stable_id, session.sandbox_ref, self._provider_name)
        for _ in range(_THAW_ATTEMPTS):
            try:
                result = self._sandbox.exec(
                    handle,
                    _THAW_ARGV,
                    as_user=LEARNER_USER,
                    env={},
                    cwd=WORKSPACE_PATH,
                    timeout_s=_THAW_TIMEOUT_S,
                )
            except SandboxGone:
                return True
            except Exception:
                _LOG.warning("thaw of session %s failed", session.stable_id, exc_info=True)
                continue
            if result.exit_code == 0 and not result.timed_out:
                return True
        return False

    def _expire(
        self, session: LabSession, action: ReapAction, now: object, counts: dict[str, int]
    ) -> None:
        to = SessionStatus.EXPIRED if action is ReapAction.EXPIRE else SessionStatus.IDLE_EXPIRED
        won = self._sessions.transition(
            session.stable_id,
            expected=ACTIVE_SESSION_STATUSES,
            to=to,
            ended_at=now,
        )
        if not won:  # ended or finished concurrently: the orphan sweep covers it
            return
        counts["expired" if action is ReapAction.EXPIRE else "idle_expired"] += 1
        if session.sandbox_ref is not None:
            handle = sandbox_handle(session.stable_id, session.sandbox_ref, self._provider_name)
            self._count_destroy(_destroy(self._sandbox, handle), counts, "expired")

    def _sweep_pods(
        self, now: object, running: dict[StableId, GradingRun], counts: dict[str, int]
    ) -> None:
        """Apply ``select_reaper_actions``: delete orphan pods, end sessions that lost theirs."""
        active = list(self._sessions.list_active())
        sessions = list(active)
        known = {s.stable_id for s in active}
        for run in running.values():  # a terminal session with a live run defers its pod
            owner = self._sessions.get(run.session_id)
            if owner is not None and owner.stable_id not in known:
                sessions.append(owner)
        pods = tuple(self._sandbox.list_sandboxes())
        failed = self._watcher.failed_pod_ids() if self._watcher is not None else frozenset()
        actions = select_reaper_actions(
            sessions,
            pods,
            now,  # type: ignore[arg-type]
            failed_pod_ids=failed,
            runs=running,
            config=self._config,
        )

        deleted = {p.sandbox_id for p in actions.delete_pods}
        counts["deferred"] += sum(
            1 for p in pods if p.sandbox_id not in deleted and p.session_stable_id not in known
        )
        for handle in actions.delete_pods:
            reason = "evicted" if handle.sandbox_id in failed else "orphan"
            self._count_destroy(_destroy(self._sandbox, handle), counts, reason)

        if self._end is None:
            return
        lost = set(actions.lost_sessions)
        for sid in actions.lost_sessions:
            if self._end.execute(sid):
                counts["infra_lost"] += 1
        # Remove the failed or evicted pod object of each lost session now (Req 19.5).
        for handle in pods:
            if handle.session_stable_id in lost and handle.sandbox_id in failed:
                self._count_destroy(_destroy(self._sandbox, handle), counts, "evicted")

    def _count_destroy(self, ok: bool, counts: dict[str, int], reason: str) -> None:
        counts["destroyed" if ok else "destroy_failures"] += 1
        if ok and self._metrics is not None:
            self._metrics.inc_reaper_deletion(reason=reason)


class DestroyOutcome:
    """Result markers of one ``lab.destroy`` delivery."""

    DESTROYED: Final = "destroyed"
    NOTHING_TO_DO: Final = "nothing-to-do"
    DEFERRED: Final = "deferred"
    FAILED: Final = "failed"


class DestroySandbox:
    """``lab.destroy`` job logic: destroy one terminal session's sandbox (idempotent)."""

    def __init__(
        self,
        *,
        sessions: LabSessionRepository,
        runs: GradingRunRepository,
        sandbox: SandboxProvider,
        clock: Clock,
        provider_name: str = "docker",
    ) -> None:
        """Wire the ports the job orchestrates."""
        self._sessions = sessions
        self._runs = runs
        self._sandbox = sandbox
        self._clock = clock
        self._provider_name = provider_name

    def execute(self, session_id: StableId) -> str:
        """Destroy the sandbox of ``session_id``; safe to deliver any number of times.

        Active sessions are left alone, and destruction is deferred while a run of the
        session is inside its deadline (the reaper finishes the job). A failed destroy
        is logged; the reaper's orphan sweep retries it.
        """
        session = self._sessions.get(session_id)
        if session is None or session.sandbox_ref is None or session.status.is_active:
            return DestroyOutcome.NOTHING_TO_DO
        now = self._clock.now()
        for run in self._runs.list_running():
            if run.session_id == session_id and now <= run.deadline_at:
                return DestroyOutcome.DEFERRED
        handle = sandbox_handle(session_id, session.sandbox_ref, self._provider_name)
        if _destroy(self._sandbox, handle):
            return DestroyOutcome.DESTROYED
        return DestroyOutcome.FAILED
