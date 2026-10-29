"""Hint-tempered mastery gain and hint-reveal recording (Req 7).

This pure, framework-free domain service owns the hint rule of the learning
engine: when a learner reveals a hint for a question *before* submitting the
attempt, the mastery gain that attempt earns is tempered by the configured hint
multiplier; a hint revealed only after submission never changes an already-
submitted attempt's gain (never applied retroactively). Like the rest of
``edu_it_domain`` it imports no web or API framework (Req 1.8,
``.kiro/steering/structure.md``) and reads time only through the injected
``Clock`` (Req 1.3).

The hint multiplier is read from :data:`edu_it_domain.config.HINT_MULTIPLIER`,
never hardcoded here (Req 7.3).

The domain rules it encodes (``.kiro/steering/domain-rules.md``, Req 7):

- **One reveal, one event (Req 7.1).** :func:`record_hint_reveal` produces exactly
  one :class:`HintReveal` (stamped at the injected ``Clock`` instant) and, through
  :func:`~edu_it_domain.learning.mastery.apply_delta`, exactly one appended
  :class:`~edu_it_domain.learning.models.MasteryEvent`.
- **Pre-submission temper (Req 7.2).** The tempered gain is
  ``int(base_gain * multiplier)`` (truncation toward zero), e.g. ``+18`` -> ``+9``.
- **Post-submission is unchanged (Req 7.4).** Without a pre-submission reveal the
  gain is exactly ``base_gain``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from edu_it_domain.config import HINT_MULTIPLIER
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Mastery
from edu_it_domain.ports.clock import Clock

__all__ = [
    "HINT_SOURCE",
    "HintReveal",
    "record_hint_reveal",
    "tempered_gain",
]

#: The default ``source`` recorded on the ``mastery_event`` a hint reveal
#: appends (Req 7.1). Named here so the string is not hardcoded at the call site.
HINT_SOURCE: str = "hint"


@dataclass(frozen=True, slots=True)
class HintReveal:
    """A recorded hint reveal (D3 §7.4, Req 7.1).

    ``revealed_at`` is the instant the learner revealed the hint, read from the
    injected ``Clock``.
    """

    revealed_at: datetime


def tempered_gain(
    base_gain: int,
    *,
    hint_revealed_before_submission: bool,
    multiplier: float = HINT_MULTIPLIER,
) -> int:
    """Return the mastery gain an attempt earns given its hint history (Req 7.2, 7.4).

    When a hint was revealed before the attempt was submitted the gain is
    ``int(base_gain * multiplier)`` (truncation toward zero); otherwise
    ``base_gain`` is returned unchanged, so the multiplier is never applied
    retroactively.
    """
    if not hint_revealed_before_submission:
        return base_gain
    return int(base_gain * multiplier)


def record_hint_reveal(
    mastery: Mastery,
    *,
    clock: Clock,
    base_gain: int,
    source: str = HINT_SOURCE,
) -> tuple[HintReveal, Mastery]:
    """Record a pre-submission hint reveal and apply the tempered gain (Req 7.1, 7.2).

    Returns exactly one :class:`HintReveal` stamped at ``clock.now()`` and a new
    :class:`~edu_it_domain.learning.models.Mastery` carrying exactly one
    additional ``MasteryEvent`` for the tempered gain. The input ``mastery`` is
    left unchanged.
    """
    reveal = HintReveal(revealed_at=clock.now())
    gain = tempered_gain(base_gain, hint_revealed_before_submission=True)
    updated = apply_delta(mastery, gain, clock=clock, source=source)
    return reveal, updated
