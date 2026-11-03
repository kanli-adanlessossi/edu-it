"""Worker job handlers for the quality loop (ai-content Req 18.4, 19.1, 19.2; task 21.2).

``ai.quality.cycle`` is periodic (``QUALITY_CYCLE_INTERVAL``, dedupe key
``ai.quality.cycle:tick:<n>`` from the scheduler). :class:`RunQualityCycle` folds the new
observations into the item statistics and then evaluates the items the fold touched.

``ai.quality.evaluate_item`` is on demand. ``SubmitProblemReport`` enqueues it best-effort with
the dedupe key ``ai.quality.evaluate_item:<kind>:<stable_id>``, and :class:`EvaluateItem`
evaluates that one item. Both flag through ``EvaluateFlags``/``FlagItem``; the Review_Task is
the only notification channel (18.8).

Handlers log and swallow unexpected errors: an unprocessed observation stays after the cursor,
and a report stays stored, so the next cycle recovers. This module imports no LLM port: reports
and statistics never reach a model (18.6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from edu_it_application.ai import QUALITY_EVALUATE_ITEM_JOB, item_from_payload

from edu_it_worker.ai.jobs import guarded_handler

if TYPE_CHECKING:
    from edu_it_application.ai import EvaluateFlags, FlagResult, FoldObservations
    from edu_it_domain.ports.item_flags import ItemKey
    from edu_it_domain.ports.task_queue import JobHandler

__all__ = [
    "QUALITY_CYCLE_JOB",
    "QUALITY_EVALUATE_ITEM_JOB",
    "EvaluateItem",
    "RunQualityCycle",
    "quality_cycle_handler",
    "quality_evaluate_item_handler",
]

QUALITY_CYCLE_JOB: Final = "ai.quality.cycle"


class RunQualityCycle:
    """Fold the pending observations, then evaluate the items they touched."""

    def __init__(self, *, fold: FoldObservations, evaluate: EvaluateFlags) -> None:
        """Wire the two use cases."""
        self._fold = fold
        self._evaluate = evaluate

    def execute(self) -> tuple[FlagResult, ...]:
        """Run one cycle; return the result of each flag made."""
        folded = self._fold.execute()
        if not folded.touched:
            return ()
        return self._evaluate.execute(folded.touched)


class EvaluateItem:
    """Evaluate one item after a new report."""

    def __init__(self, *, evaluate: EvaluateFlags) -> None:
        """Wire the use case."""
        self._evaluate = evaluate

    def execute(self, key: ItemKey) -> tuple[FlagResult, ...]:
        """Flag ``key`` when its reports or statistics cross a threshold."""
        return self._evaluate.execute([key])


def quality_cycle_handler(use_case: RunQualityCycle) -> JobHandler:
    """``ai.quality.cycle``: periodic, no payload."""
    return guarded_handler(QUALITY_CYCLE_JOB, lambda _p: use_case.execute())


def quality_evaluate_item_handler(use_case: EvaluateItem) -> JobHandler:
    """``ai.quality.evaluate_item``: ``{"item_kind", "item_id"}``."""
    return guarded_handler(
        QUALITY_EVALUATE_ITEM_JOB, lambda payload: use_case.execute(item_from_payload(payload))
    )
