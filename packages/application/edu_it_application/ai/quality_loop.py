"""The quality loop use cases (ai-content Req 18.4, 18.7 to 18.9, 19.1 to 19.5, 20.2; task 21.1).

``FoldObservations`` folds new answers into the item statistics. ``EvaluateFlags`` judges the
items a fold or a new report touched and calls ``FlagItem`` for each one that crossed a
threshold. ``FlagItem`` takes an item out of serving and opens the Review_Task that is the only
notification channel (18.8).

``FlagItem`` is idempotent. It acts only when the item has no active flag, and the flag is
cleared only by a reviewer decision on the item's Review_Task (``ReviewTaskActions.approve``).
A rejected flagged item keeps its flag, so a later evaluation neither serves it again nor opens a
second task. The steps are ordered so that a crash never leaves a servable flagged item and a
rerun repairs a partial run: the open task is added first (idempotent per open target), then
the flag, then the variant is withdrawn.

This module imports no LLM port: report comments and statistics never reach a model (18.6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from edu_it_domain.ai.lifecycle import SERVABLE_STATES, VariantState, transition
from edu_it_domain.ai.quality import ItemStat, fold, is_anomalous, reports_reach_threshold
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from edu_it_domain.ports.id_generator import IdGenerator
    from edu_it_domain.ports.item_info import ItemInfoSource
    from edu_it_domain.ports.item_quality import ItemQualityRepository
    from edu_it_domain.ports.problem_reports import ProblemReportRepository
    from edu_it_domain.ports.review_tasks import ReviewTaskRepository
    from edu_it_domain.ports.variants import Variant, VariantRepository

__all__ = [
    "EvaluateFlags",
    "FlagItem",
    "FlagOutcome",
    "FlagResult",
    "FoldObservations",
    "FoldResult",
    "flag_reason_text",
]

_LOG = logging.getLogger(__name__)

#: How many observations one ``FoldObservations`` batch reads.
DEFAULT_FOLD_BATCH = 500


def flag_reason_text(reason: FlagReason) -> str:
    """The text stored on the Review_Task and on a withdrawn variant."""
    return f"{reason.kind.value}: {reason.detail}" if reason.detail else reason.kind.value


# -- FoldObservations -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoldResult:
    """What a fold did."""

    touched: frozenset[ItemKey]
    """Items whose statistics changed."""

    folded: int
    """How many observations were folded."""

    cursor: int
    """The cursor after the fold."""


class FoldObservations:
    """Fold the observations after the cursor into the item statistics."""

    def __init__(
        self, *, quality: ItemQualityRepository, batch_size: int = DEFAULT_FOLD_BATCH
    ) -> None:
        """Wire the repository. ``batch_size`` bounds one read and one transaction."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._quality = quality
        self._batch_size = batch_size

    def execute(self) -> FoldResult:
        """Fold every pending observation; each batch stores its statistics and cursor together."""
        cursor = self._quality.cursor()
        touched: set[ItemKey] = set()
        folded = 0
        while True:
            batch = self._quality.observations_after(cursor, self._batch_size)
            if not batch:
                break
            stats: dict[ItemKey, ItemStat] = {}
            for stored in batch:
                current = stats.get(stored.key)
                if current is None:
                    current = self._quality.get(stored.key) or ItemStat()
                stats[stored.key] = fold(current, stored.as_observation())
            cursor = max(stored.id for stored in batch)
            self._quality.apply(stats, cursor)
            touched.update(stats)
            folded += len(batch)
            if len(batch) < self._batch_size:
                break
        return FoldResult(frozenset(touched), folded, cursor)


# -- FlagItem -------------------------------------------------------------------------------


class FlagOutcome(StrEnum):
    """How a ``FlagItem`` call ended."""

    FLAGGED = "flagged"
    ALREADY_FLAGGED = "already_flagged"
    UNKNOWN_ITEM = "unknown_item"
    NOT_SERVABLE = "not_servable"
    """A variant outside Approved and Served is not in serving: nothing to withdraw."""


@dataclass(frozen=True, slots=True)
class FlagResult:
    """What ``FlagItem`` did."""

    outcome: FlagOutcome
    task_stable_id: str | None = None
    refill_parent: str | None = None
    """The parent whose refill was requested because its pool emptied."""


class FlagItem:
    """Take an item out of serving and open the one Review_Task that settles it."""

    def __init__(
        self,
        *,
        flags: ItemQualityRepository,
        tasks: ReviewTaskRepository,
        variants: VariantRepository,
        items: ItemInfoSource,
        ids: IdGenerator,
        request_refill: Callable[[str], None] | None = None,
    ) -> None:
        """Wire the ports.

        ``request_refill`` enqueues a refill job for a Parent_Question (the worker passes its
        ``ai.refill.parent`` enqueue). A failure to enqueue is logged and never undoes the flag:
        the next refill cycle finds the empty pool anyway (20.2).
        """
        self._flags = flags
        self._tasks = tasks
        self._variants = variants
        self._items = items
        self._ids = ids
        self._request_refill = request_refill

    def execute(self, key: ItemKey, reason: FlagReason) -> FlagResult:
        """Flag ``key`` for ``reason``; a second call for a flagged item changes nothing."""
        variant: Variant | None = None
        if key.kind is ItemKind.VARIANT:
            variant = self._variants.get(key.stable_id)
            if variant is None:
                return FlagResult(FlagOutcome.UNKNOWN_ITEM)
            track = variant.track_stable_id
        else:
            info = self._items.describe(key)
            if info is None:
                return FlagResult(FlagOutcome.UNKNOWN_ITEM)
            track = info.track_stable_id

        if self._flags.active_flags([key]):
            return FlagResult(FlagOutcome.ALREADY_FLAGGED)
        if variant is not None and variant.state not in SERVABLE_STATES:
            return FlagResult(FlagOutcome.NOT_SERVABLE)

        task = self._tasks.add(
            NewReviewTask(
                stable_id=self._ids.new_id(),
                kind=TaskKind.FLAGGED_ITEM,
                target_kind=TargetKind(key.kind.value),
                target_stable_id=key.stable_id,
                track_stable_id=track,
                reason=flag_reason_text(reason),
            )
        )
        if not self._flags.flag(key, reason):  # lost a race with another worker
            return FlagResult(FlagOutcome.ALREADY_FLAGGED, task.stable_id)

        refill_parent: str | None = None
        if variant is not None:
            refill_parent = self._withdraw(variant, reason)
        return FlagResult(FlagOutcome.FLAGGED, task.stable_id, refill_parent)

    def _withdraw(self, variant: Variant, reason: FlagReason) -> str | None:
        """Set the variant ``Withdrawn``; return the parent when its pool is now empty."""
        current = variant
        if current.state is VariantState.APPROVED:  # never marked served: it passes through Served
            current = replace(current, state=transition(current.state, VariantState.SERVED))
            self._variants.save(current)
        self._variants.save(
            replace(
                current,
                state=transition(current.state, VariantState.WITHDRAWN),
                withdrawn_reason=flag_reason_text(reason),
            )
        )
        parent = variant.parent_question_stable_id
        if self._variants.count_approved(parent) > 0:
            return None
        if self._request_refill is not None:
            try:
                self._request_refill(parent)
            except Exception:
                _LOG.warning("could not request a refill for %s", parent, exc_info=True)
                return None
        return parent


# -- EvaluateFlags --------------------------------------------------------------------------


class EvaluateFlags:
    """Flag the touched items whose reports or statistics cross a threshold."""

    def __init__(
        self,
        *,
        quality: ItemQualityRepository,
        reports: ProblemReportRepository,
        items: ItemInfoSource,
        flag_item: FlagItem,
    ) -> None:
        """Wire the ports."""
        self._quality = quality
        self._reports = reports
        self._items = items
        self._flag_item = flag_item

    def execute(self, keys: Iterable[ItemKey]) -> tuple[FlagResult, ...]:
        """Evaluate ``keys``; return the result of each ``FlagItem`` call made."""
        wanted = sorted(set(keys), key=lambda k: (k.kind.value, k.stable_id))
        already = self._quality.active_flags(wanted)
        results: list[FlagResult] = []
        for key in wanted:
            if key in already:
                continue
            reason = self._reason(key)
            if reason is not None:
                results.append(self._flag_item.execute(key, reason))
        return tuple(results)

    def _reason(self, key: ItemKey) -> FlagReason | None:
        parts: list[str] = []
        kind: FlagReasonKind | None = None

        info = self._items.describe(key)
        version = None if info is None else info.content_version
        if version is not None:
            reporters = self._reports.reporters_for(key, version)
            if reports_reach_threshold(((r, version) for r in reporters), version):
                kind = FlagReasonKind.REPORTS
                parts.append(f"reports={len(reporters)}")

        stat = self._quality.get(key)
        if stat is not None and is_anomalous(stat):
            kind = kind or FlagReasonKind.STATISTICS
            rate = stat.correct_rate or 0.0
            avg = stat.avg_time_ms
            parts.append(
                f"samples={stat.samples} correct_rate={rate:.3f}"
                + ("" if avg is None else f" avg_time_ms={avg:.0f}")
            )
        if kind is None:
            return None
        return FlagReason(kind, "; ".join(parts))
