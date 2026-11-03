"""Worker job handlers for the path advisor (ai-content Req 17.2; task 22.3).

``ai.advise.cycle`` is periodic (``ADVISOR_CYCLE_INTERVAL``, dedupe key
``ai.advise.cycle:tick:<n>`` from the scheduler). :class:`ScanAdvisorLearners` enqueues one
``ai.advise.user`` job per learner with activity, with the dedupe key
``ai.advise.user:<user>:<bucket>``; the bucket is the cycle's time bucket, so a learner gets at
most one pending job per cycle.

:class:`AdviseUser` is the ``ai.advise.user`` logic. It opens a ledger job of kind ``ADVISOR``
so that the Budgets apply, runs ``RunPathAdvisor`` and closes the job. ``RunPathAdvisor`` turns
every model, budget or order problem into a persisted rule-based recommendation, so a run never
leaves a learner without one. Handlers log and swallow unexpected errors, because the next
cycle recovers every job.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Protocol

from edu_it_domain import config as domain_config
from edu_it_domain.ports.generation_ledger import JobKind, JobStatus, NewJob
from edu_it_infrastructure.llm.ledger_binding import JobScope

from edu_it_worker.ai.jobs import guarded_handler

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.ai.path_advisor import AdvisorRunResult, RunPathAdvisor
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.generation_ledger import GenerationLedger
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue

__all__ = [
    "ADVISE_CYCLE_JOB",
    "ADVISE_USER_JOB",
    "AdviseUser",
    "AdvisorTargets",
    "ScanAdvisorLearners",
    "advise_bucket",
    "advise_cycle_handler",
    "advise_user_dedupe_key",
    "advise_user_handler",
]

_LOG = logging.getLogger(__name__)

ADVISE_CYCLE_JOB: Final = "ai.advise.cycle"
ADVISE_USER_JOB: Final = "ai.advise.user"

_USER_KEY: Final = "user_stable_id"


class AdvisorTargets(Protocol):
    """Who the advisor runs for, and over which concepts."""

    def active_learners(self) -> tuple[str, ...]:
        """The ``stable_id`` of each learner with activity."""
        ...

    def candidate_concepts(self) -> tuple[str, ...]:
        """The concept ids the advisor may recommend from (eligibility is checked later)."""
        ...


def advise_bucket(clock: Clock, config: object = domain_config) -> int:
    """The Advisor_Cycle bucket of the current time (same arithmetic as the ``Scheduler``)."""
    interval: float = config.ADVISOR_CYCLE_INTERVAL.total_seconds()  # type: ignore[attr-defined]
    return int(clock.now().timestamp() // interval)


def advise_user_dedupe_key(user_stable_id: str, bucket: int) -> str:
    """Dedupe key of the advisor job of one learner in one cycle bucket."""
    return f"{ADVISE_USER_JOB}:{user_stable_id}:{bucket}"


class ScanAdvisorLearners:
    """Enqueue ``ai.advise.user`` for every learner with activity."""

    def __init__(
        self,
        *,
        targets: AdvisorTargets,
        queue: TaskQueue,
        clock: Clock,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the scan uses."""
        self._targets = targets
        self._queue = queue
        self._clock = clock
        self._config = config

    def execute(self) -> list[str]:
        """Enqueue the due runs; return the ``stable_id`` of each learner enqueued."""
        bucket = advise_bucket(self._clock, self._config)
        enqueued: list[str] = []
        for user_id in self._targets.active_learners():
            try:
                self._queue.enqueue(
                    ADVISE_USER_JOB,
                    {_USER_KEY: user_id},
                    dedupe_key=advise_user_dedupe_key(user_id, bucket),
                )
            except Exception:  # one failure must not starve the other learners
                _LOG.warning("could not enqueue an advisor run for a learner", exc_info=True)
                continue
            enqueued.append(user_id)
        return enqueued


class AdviseUser:
    """Run the advisor for one learner under a ledger job."""

    def __init__(
        self,
        *,
        targets: AdvisorTargets,
        ledger: GenerationLedger,
        clock: Clock,
        ids: IdGenerator,
        build_advisor: Callable[[JobScope], RunPathAdvisor],
        prompt_version: str = "1",
    ) -> None:
        """Wire the ports. ``build_advisor`` binds the provider to the job's scope."""
        self._targets = targets
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._build_advisor = build_advisor
        self._prompt_version = prompt_version

    def execute(self, user_stable_id: str) -> AdvisorRunResult:
        """Run the advisor over the candidate concepts and persist the recommendation."""
        job = self._ledger.open_job(
            NewJob(
                stable_id=self._ids.new_id(),
                kind=JobKind.ADVISOR,
                prompt_id="advisor",
                prompt_version=self._prompt_version,
            )
        )
        scope = JobScope(ledger=self._ledger, job=job, clock=self._clock)
        try:
            result = self._build_advisor(scope).execute(
                user_stable_id, self._targets.candidate_concepts()
            )
        except BaseException:
            self._ledger.close_job(job, JobStatus.FAILED)
            raise
        self._ledger.close_job(job, JobStatus.COMPLETED)
        return result


def advise_cycle_handler(use_case: ScanAdvisorLearners) -> JobHandler:
    """``ai.advise.cycle``: periodic, no payload."""
    return guarded_handler(ADVISE_CYCLE_JOB, lambda _p: use_case.execute())


def advise_user_handler(use_case: AdviseUser) -> JobHandler:
    """``ai.advise.user``: ``{"user_stable_id"}``."""

    def run(payload: JobPayload) -> None:
        user_id = payload.get(_USER_KEY)
        if not isinstance(user_id, str) or not user_id:
            raise ValueError(f"payload is missing {_USER_KEY!r}")
        use_case.execute(user_id)

    return guarded_handler(ADVISE_USER_JOB, run)
