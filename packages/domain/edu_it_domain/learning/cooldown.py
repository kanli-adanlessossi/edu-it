"""Quiz-attempt cooldown decisions (Req 4.1, 4.2, 4.3).

This pure, framework-free domain service reports whether a quiz is in cooldown
from the learner's last-attempt time, the injected ``Clock``, and the configured
cooldown interval. Like the rest of ``edu_it_domain`` it imports no web or API
framework (Req 1.8, ``.kiro/steering/structure.md``) and reads time only through
the injected ``Clock`` (Req 1.3).

The interval is read from :data:`~edu_it_domain.config.COOLDOWN_INTERVAL`, never
hardcoded here (Req 1.3, 4.3). It is exposed as an optional keyword argument
defaulting to that config value so a caller can override it.

The domain rules it encodes (Req 4):

- **In cooldown (Req 4.1).** While the Clock reading is strictly before
  ``last_attempt_at + interval`` the quiz is reported in cooldown.
- **Attemptable (Req 4.2).** At exactly ``last_attempt_at + interval`` and after,
  the quiz is attemptable again.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from edu_it_domain.config import COOLDOWN_INTERVAL
from edu_it_domain.ports.clock import Clock

__all__ = [
    "attemptable_at",
    "is_in_cooldown",
]


def attemptable_at(
    last_attempt_at: datetime,
    *,
    interval: timedelta = COOLDOWN_INTERVAL,
) -> datetime:
    """Return the instant the quiz becomes attemptable again (Req 4.2, 4.3).

    This is exactly ``last_attempt_at + interval`` and needs no Clock because it
    is the boundary itself. ``interval`` defaults to
    :data:`~edu_it_domain.config.COOLDOWN_INTERVAL`.
    """
    return last_attempt_at + interval


def is_in_cooldown(
    last_attempt_at: datetime,
    *,
    clock: Clock,
    interval: timedelta = COOLDOWN_INTERVAL,
) -> bool:
    """Return whether the quiz is in cooldown (Req 4.1, 4.2, 4.3).

    ``True`` if and only if ``clock.now()`` is strictly before
    :func:`attemptable_at`; at exactly the boundary the quiz is attemptable, so
    this returns ``False``.
    """
    return clock.now() < attemptable_at(last_attempt_at, interval=interval)
