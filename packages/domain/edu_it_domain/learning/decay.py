"""Deterministic, idempotent spaced-review decay (Req 8, ADR A6).

Pure, framework-free domain service. Time is read only through the injected
``Clock`` (Req 1.3). Every business number (the -1-per-overdue-day rate, the 30
floor, the 0-100 bounds) comes from :mod:`edu_it_domain.config` via the mastery
service this module wraps; nothing is hardcoded here.

Rules encoded:

- **Due (Req 9.6).** A concept is due iff ``clock.now() >= due_at`` (inclusive).
- **Overdue days (Req 8.1, 9.6).** The count of *complete* 24-hour days by which
  the Clock reading exceeds ``due_at``; never negative.
- **Decay (Req 8.1, 8.2, 8.5).** Mastery drops by ``DECAY_RATE_PER_DAY`` per
  complete overdue day, floored/banded by :func:`mastery.apply_decay`.
- **Idempotent per instant (Req 8.4).** The target reduction is a function of
  ``(due_at, now)`` only. Decay already recorded for the current overdue
  period (decay-sourced mastery events at or after ``due_at``) is subtracted,
  so re-running at the same instant applies nothing further, and a skipped
  worker run is recovered on the next without double-counting.
- **Re-queue, not relock (Req 8.3, ADR A6).** This module only adjusts the
  mastery value. It never touches unlock state, and never mutates the schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from edu_it_domain.config import DECAY_RATE_PER_DAY
from edu_it_domain.learning import mastery as mastery_service
from edu_it_domain.learning.mastery import DECAY_SOURCE
from edu_it_domain.learning.models import Mastery, ReviewSchedule
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.lab_check import LabCheck

__all__ = ["apply_decay", "is_due", "overdue_days"]

#: One complete overdue day: the unit Req 8.1 / 9.6 count in.
_OVERDUE_DAY: timedelta = timedelta(days=1)


def is_due(due_at: datetime, *, clock: Clock) -> bool:
    """Return whether the concept is due: ``clock.now() >= due_at`` (Req 9.6)."""
    return clock.now() >= due_at


def overdue_days(due_at: datetime, *, clock: Clock) -> int:
    """Return the whole 24-hour days the Clock exceeds ``due_at``; 0 if not due (Req 9.6, 8.1)."""
    elapsed = clock.now() - due_at
    if elapsed < timedelta(0):
        return 0
    return elapsed // _OVERDUE_DAY


def apply_decay(
    mastery: Mastery,
    schedule: ReviewSchedule,
    *,
    clock: Clock,
    source: str = DECAY_SOURCE,
    lab_check: LabCheck | None = None,
) -> Mastery:
    """Apply decay for ``schedule`` at the Clock instant; idempotent (Req 8.1, 8.4).

    Returns ``mastery`` unchanged when no further reduction is owed (not overdue,
    already applied for this instant, or already at the floor). Otherwise
    returns a new :class:`Mastery` with exactly one decay event appended.
    """
    points_per_day = -DECAY_RATE_PER_DAY
    target_points = points_per_day * overdue_days(schedule.due_at, clock=clock)

    # Decay already recorded in the current overdue period (since due_at).
    applied_points = sum(
        -event.delta
        for event in mastery.events
        if event.source == source and event.occurred_at >= schedule.due_at
    )

    remaining_days = (target_points - applied_points) // points_per_day
    if remaining_days <= 0:
        return mastery

    decayed = mastery_service.apply_decay(
        mastery,
        remaining_days,
        clock=clock,
        source=source,
        lab_check=lab_check,
    )
    # Floored: nothing actually changed, so record nothing (keeps re-runs pure).
    if decayed.score == mastery.score:
        return mastery
    return decayed
