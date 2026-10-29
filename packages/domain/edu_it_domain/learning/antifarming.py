"""Anti-farming decision for repeated answers (Req 6, 1.3).

This pure, framework-free domain service decides whether a re-answer of a
question counts toward mastery. The window is anchored at the question's
``first_answered_at`` and measured by the injected ``Clock`` (never the wall
clock; Req 6.4, 1.3). Like the rest of ``edu_it_domain`` it imports no web or
API framework (Req 1.8, ``.kiro/steering/structure.md``).

The 24-hour window length is read from
:data:`~edu_it_domain.config.ANTI_FARMING_WINDOW`, never hardcoded here
(Req 1.3, 6.4).

The domain rules it encodes (``.kiro/steering/domain-rules.md``, Req 6):

- **Inside the window (Req 6.1, 6.2).** A re-answer at or after
  ``first_answered_at`` and strictly before ``first_answered_at + window`` earns
  zero additional mastery and is recorded with ``counted_for_mastery`` false.
- **At or after the boundary (Req 6.5).** A re-answer exactly at
  ``first_answered_at + window`` or later is evaluated under the standard
  mastery rules, with ``counted_for_mastery`` true.

The window is therefore the half-open interval
``[first_answered_at, first_answered_at + window)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from edu_it_domain.config import ANTI_FARMING_WINDOW
from edu_it_domain.learning.models import Answer, StableId
from edu_it_domain.ports.clock import Clock

__all__ = [
    "counts_for_mastery",
    "evaluate_reanswer",
]


def counts_for_mastery(
    first_answered_at: datetime,
    *,
    clock: Clock,
    window: timedelta = ANTI_FARMING_WINDOW,
) -> bool:
    """Return whether a re-answer made now counts toward mastery (Req 6.1, 6.5).

    The re-answer time is read from ``clock``. It is inside the half-open window
    ``[first_answered_at, first_answered_at + window)`` (and so does not count)
    when it is strictly before ``first_answered_at + window``; at or after that
    boundary the standard mastery rules apply and it counts.
    """
    return clock.now() >= first_answered_at + window


def evaluate_reanswer(
    question_id: StableId,
    first_answered_at: datetime,
    *,
    clock: Clock,
    window: timedelta = ANTI_FARMING_WINDOW,
) -> Answer:
    """Build the repeat :class:`Answer` for a re-answer evaluated now (Req 6.2, 6.5).

    The result keeps the same ``question_id`` and ``first_answered_at`` anchor
    (Req 6.4) and sets ``counted_for_mastery`` to exactly
    :func:`counts_for_mastery`: false inside the window, true at or after it.
    """
    return Answer(
        question_id=question_id,
        first_answered_at=first_answered_at,
        counted_for_mastery=counts_for_mastery(first_answered_at, clock=clock, window=window),
    )
