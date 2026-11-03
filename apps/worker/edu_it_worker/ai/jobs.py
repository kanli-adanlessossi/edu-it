"""Worker job handlers for the variant refill (ai-content Req 13.1, 13.5, 13.8 to 13.10).

``ai.refill.cycle`` is periodic (``REFILL_CYCLE_INTERVAL``). :class:`ScanRefillParents` finds
the Approved parent questions whose pool is below the ``VARIANT_POOL_THRESHOLD`` and enqueues
one ``ai.refill.parent`` job each, with the dedupe key ``ai.refill.parent:<parent>:<bucket>``.
The bucket is the cycle's time bucket, so a parent gets at most one pending job per cycle.

:class:`RefillParent` is the ``ai.refill.parent`` logic. It opens a ledger job so that the
Budgets and the retry bound apply (13.5), runs ``RefillVariantPool`` and closes the job. A run
that stops on malformed output, an unavailable provider or a reached Budget ends there: it
enqueues nothing (the pool stays below the threshold until the next cycle, 13.8, 13.9) and
raises no alert, email or escalation (13.10). Handlers log and swallow unexpected errors,
because the next cycle recovers every job.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from edu_it_application.ai import RefillOutcome
from edu_it_domain import config as domain_config
from edu_it_domain.ports.generation_ledger import JobKind, JobStatus, NewJob
from edu_it_domain.ports.item_flags import ItemKey, ItemKind
from edu_it_infrastructure.llm.ledger_binding import JobScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from edu_it_application.ai import RefillResult, RefillVariantPool
    from edu_it_domain.ports.clock import Clock
    from edu_it_domain.ports.generation_ledger import GenerationLedger
    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.item_flags import ItemFlagReader
    from edu_it_domain.ports.parent_questions import ParentQuestionSource
    from edu_it_domain.ports.task_queue import JobHandler, JobPayload, TaskQueue
    from edu_it_domain.ports.variants import VariantRepository

__all__ = [
    "REFILL_CYCLE_JOB",
    "REFILL_PARENT_JOB",
    "RefillParent",
    "ScanRefillParents",
    "guarded_handler",
    "refill_bucket",
    "refill_cycle_handler",
    "refill_parent_dedupe_key",
    "refill_parent_handler",
]

_LOG = logging.getLogger(__name__)

REFILL_CYCLE_JOB: Final = "ai.refill.cycle"
REFILL_PARENT_JOB: Final = "ai.refill.parent"

_PARENT_KEY: Final = "parent_stable_id"


def refill_bucket(clock: Clock, config: object = domain_config) -> int:
    """The Refill_Cycle bucket of the current time (same arithmetic as the ``Scheduler``)."""
    interval: float = config.REFILL_CYCLE_INTERVAL.total_seconds()  # type: ignore[attr-defined]
    return int(clock.now().timestamp() // interval)


def refill_parent_dedupe_key(parent_stable_id: str, bucket: int) -> str:
    """Dedupe key of the refill job of one parent in one cycle bucket."""
    return f"{REFILL_PARENT_JOB}:{parent_stable_id}:{bucket}"


class ScanRefillParents:
    """Enqueue ``ai.refill.parent`` for every Approved, unflagged parent below the threshold."""

    def __init__(
        self,
        *,
        parents: ParentQuestionSource,
        variants: VariantRepository,
        flags: ItemFlagReader,
        queue: TaskQueue,
        clock: Clock,
        config: object = domain_config,
    ) -> None:
        """Wire the ports the scan uses."""
        self._parents = parents
        self._variants = variants
        self._flags = flags
        self._queue = queue
        self._clock = clock
        self._config = config

    def execute(self) -> list[str]:
        """Enqueue the due refills; return the ``stable_id`` of each parent enqueued."""
        threshold: int = self._config.VARIANT_POOL_THRESHOLD  # type: ignore[attr-defined]
        bucket = refill_bucket(self._clock, self._config)
        enqueued: list[str] = []
        for parent_id in self._parents.approved_ids():
            if self._variants.count_approved(parent_id) >= threshold:
                continue
            if self._flags.active_flags([ItemKey(ItemKind.QUESTION, parent_id)]):
                continue
            try:
                self._queue.enqueue(
                    REFILL_PARENT_JOB,
                    {_PARENT_KEY: parent_id},
                    dedupe_key=refill_parent_dedupe_key(parent_id, bucket),
                )
            except Exception:  # one failure must not starve the other parents
                _LOG.warning("could not enqueue a refill for %s", parent_id, exc_info=True)
                continue
            enqueued.append(parent_id)
        return enqueued


class RefillParent:
    """Run one refill of one parent question under a ledger job."""

    def __init__(
        self,
        *,
        parents: ParentQuestionSource,
        ledger: GenerationLedger,
        clock: Clock,
        ids: IdGenerator,
        build_pool: Callable[[JobScope], RefillVariantPool],
        prompt_version: str = "1",
    ) -> None:
        """Wire the ports. ``build_pool`` binds the providers to the job's scope."""
        self._parents = parents
        self._ledger = ledger
        self._clock = clock
        self._ids = ids
        self._build_pool = build_pool
        self._prompt_version = prompt_version

    def execute(self, parent_stable_id: str) -> RefillResult | None:
        """Refill the parent; ``None`` when the parent is unknown or not Approved."""
        parent = self._parents.get(parent_stable_id)
        if parent is None or not parent.approved:
            return None
        job = self._ledger.open_job(
            NewJob(
                stable_id=self._ids.new_id(),
                kind=JobKind.REFILL,
                track_stable_id=parent.track_stable_id,
                prompt_id="variant",
                prompt_version=self._prompt_version,
            )
        )
        scope = JobScope(
            ledger=self._ledger,
            job=job,
            clock=self._clock,
            track_stable_id=parent.track_stable_id,
        )
        try:
            result = self._build_pool(scope).execute(parent_stable_id)
        except BaseException:
            self._ledger.close_job(job, JobStatus.FAILED)
            raise
        self._ledger.close_job(job, _status(result))
        return result


def _status(result: RefillResult) -> JobStatus:
    if result.outcome is not RefillOutcome.STOPPED:
        return JobStatus.COMPLETED
    if result.stop_reason == "BudgetReachedError":
        return JobStatus.BUDGET_REACHED
    return JobStatus.FAILED


def guarded_handler(name: str, body: Callable[[JobPayload], object]) -> JobHandler:
    """Wrap ``body`` so an unexpected error is logged and swallowed (the next cycle recovers)."""

    def handler(payload: JobPayload) -> None:
        try:
            body(payload)
        except Exception:
            _LOG.warning("job %s failed; the next cycle recovers it", name, exc_info=True)

    return handler


def refill_cycle_handler(use_case: ScanRefillParents) -> JobHandler:
    """``ai.refill.cycle``: periodic, no payload."""
    return guarded_handler(REFILL_CYCLE_JOB, lambda _p: use_case.execute())


def refill_parent_handler(use_case: RefillParent) -> JobHandler:
    """``ai.refill.parent``: ``{"parent_stable_id"}``."""

    def run(payload: JobPayload) -> None:
        parent_id = payload.get(_PARENT_KEY)
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError(f"payload is missing {_PARENT_KEY!r}")
        use_case.execute(parent_id)

    return guarded_handler(REFILL_PARENT_JOB, run)
