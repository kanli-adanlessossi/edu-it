"""Mastery delta, state-band, and decay decisions (Req 5, 1.4).

This pure, framework-free domain service owns the mastery rules of the learning
engine: it derives a concept's :class:`~edu_it_domain.learning.models.MasteryState`
from an integer score and the Mastered-gate conditions, applies a signed mastery
delta (capping at the configured bounds and appending exactly one append-only
``mastery_event``), and applies spaced-review decay (flooring a once-Practiced
concept at the configured floor and stepping the state one band down as
thresholds are crossed). Like the rest of ``edu_it_domain`` it imports no web or
API framework (Req 1.8, ``.kiro/steering/structure.md``) and reads time only
through the injected ``Clock`` (Req 1.3).

Every business number it needs — the 30 / 60 / 85 state thresholds, the 0 / 100
mastery bounds, the -1-per-overdue-day decay rate, and the 30 floor a once-
Practiced concept never decays below — is read from
:mod:`edu_it_domain.config`, never hardcoded here (Req 1.3,
``.kiro/steering/structure.md``). The domain types it computes over live in
:mod:`edu_it_domain.learning.models`, frozen so every result is a new immutable
value and the input is left unchanged.

The domain rules it encodes (``.kiro/steering/domain-rules.md``, Req 5):

- **Bounds (Req 5.1, 5.8).** The mastery score is an integer in the inclusive
  range :data:`~edu_it_domain.config.MASTERY_MIN`-:data:`~edu_it_domain.config.MASTERY_MAX`
  (0-100). :func:`apply_delta` caps a computed value above the max and floors a
  value below the min.
- **State bands (Req 5.2, 5.3, 5.5).** The thresholds in
  :data:`~edu_it_domain.config.MASTERY_THRESHOLDS` partition the scale with
  inclusive lower bounds: below ``practiced`` is ``Introduced`` (or ``Unseen``
  when not yet encountered); ``practiced``..``proficient`` - 1 is ``Practiced``;
  ``proficient``..``mastered`` - 1 is ``Proficient``.
- **The Mastered gate (Req 5.6, 1.4).** A score at or above the ``mastered``
  threshold reaches ``Mastered`` only when a review has passed **and** the
  lab-check path is satisfied — no lab covers the concept, or a covering lab's
  check has passed for the learner (consulted through the
  :class:`~edu_it_domain.ports.lab_check.LabCheck` port). Otherwise the numeric
  score alone leaves the state at ``Proficient``.
- **One event per change (Req 5.7).** Every applied change appends exactly one
  :class:`~edu_it_domain.learning.models.MasteryEvent` recording the *effective*
  delta actually applied (after cap/floor), its source, and the instant read
  from the injected ``Clock``.
- **Decay floor and transitions (Req 5.9, 5.10, 5.11, 8.2, 8.5).** Decay lowers
  mastery by the per-day rate for each complete overdue day, flooring at the
  "never below 30 once Practiced" floor so decay never drops a once-Practiced
  concept below that floor nor below the Practiced band, and steps the state at
  most one band down as a threshold is crossed (Mastered->Proficient,
  Proficient->Practiced). A decayed concept never re-satisfies the Mastered gate
  by sitting still, so decay's resulting state is at most ``Proficient``.
"""

from __future__ import annotations

from edu_it_domain.config import (
    DECAY_FLOOR_ONCE_PRACTICED,
    DECAY_RATE_PER_DAY,
    MASTERY_MAX,
    MASTERY_MIN,
    MASTERY_THRESHOLDS,
)
from edu_it_domain.learning.models import (
    Mastery,
    MasteryEvent,
    MasteryState,
    StableId,
)
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.lab_check import LabCheck

__all__ = [
    "apply_decay",
    "apply_delta",
    "derive_state",
]

#: The default ``source`` recorded on the ``mastery_event`` a decay change
#: appends (Req 5.7). Named here so the string is not hardcoded at the call site
#: and reads the same way the other services name their event sources.
DECAY_SOURCE: str = "decay"


def _lab_check_satisfied(
    lab_check: LabCheck | None,
    *,
    concept_id: StableId | None,
    user_id: StableId | None,
) -> bool:
    """Return whether the Mastered gate's lab-check condition holds (Req 5.6, 1.4).

    The condition is satisfied when no ``lab_check`` port is supplied, when the
    supplied port reports no lab covers the concept, or when a covering lab's
    check has passed for the learner. When the port is supplied but either
    identifier is missing the concept cannot be looked up, so the lab-covered
    case cannot be confirmed and the condition is treated as unmet.
    """
    if lab_check is None:
        return True
    if concept_id is None or user_id is None:
        return False
    if not lab_check.covers(concept_id):
        return True
    return lab_check.passed(concept_id, user_id)


def derive_state(
    score: int,
    *,
    encountered: bool = True,
    ever_practiced: bool = False,
    review_passed: bool = False,
    lab_check: LabCheck | None = None,
    concept_id: StableId | None = None,
    user_id: StableId | None = None,
) -> MasteryState:
    """Return the :class:`MasteryState` for ``score`` and the gate conditions (Req 5.2-5.6).

    Classifies ``score`` into its threshold band from
    :data:`~edu_it_domain.config.MASTERY_THRESHOLDS` (inclusive lower bounds):

    - below ``practiced``: :attr:`~MasteryState.INTRODUCED` when the concept has
      been ``encountered``, otherwise :attr:`~MasteryState.UNSEEN` (Req 5.2).
    - ``practiced``..``proficient`` - 1: :attr:`~MasteryState.PRACTICED` (Req 5.3).
    - ``proficient``..``mastered`` - 1: :attr:`~MasteryState.PROFICIENT` (Req 5.5).
    - at or above ``mastered``: :attr:`~MasteryState.MASTERED` only when
      ``review_passed`` is true **and** the lab-check path is satisfied
      (``lab_check`` is ``None``, or reports the concept uncovered, or reports a
      covering lab passed for the learner); otherwise the numeric Mastered band
      with the gate unmet stays :attr:`~MasteryState.PROFICIENT` (Req 5.6, 1.4).

    ``ever_practiced`` is accepted for callers that track whether a concept has
    ever reached Practiced; it does not change the pure band classification here
    (the decay transitions that depend on prior state are handled by
    :func:`apply_decay`).
    """
    del ever_practiced  # Part of the pinned public API; does not affect the band. See docstring.
    thresholds = MASTERY_THRESHOLDS

    if score < thresholds.practiced:
        return MasteryState.INTRODUCED if encountered else MasteryState.UNSEEN
    if score < thresholds.proficient:
        return MasteryState.PRACTICED
    if score < thresholds.mastered:
        return MasteryState.PROFICIENT

    # Numeric Mastered band: the state reaches MASTERED only when the full gate
    # is met, otherwise it stays at the top of the Proficient band (Req 5.6).
    gate_met = review_passed and _lab_check_satisfied(
        lab_check, concept_id=concept_id, user_id=user_id
    )
    return MasteryState.MASTERED if gate_met else MasteryState.PROFICIENT


def _append_event(mastery: Mastery, event: MasteryEvent) -> tuple[MasteryEvent, ...]:
    """Return ``mastery``'s event history with ``event`` appended (Req 5.7)."""
    return (*mastery.events, event)


def apply_delta(
    mastery: Mastery,
    delta: int,
    *,
    clock: Clock,
    source: str,
    review_passed: bool = False,
    lab_check: LabCheck | None = None,
) -> Mastery:
    """Apply a signed ``delta`` to ``mastery`` and return the new value (Req 5.1, 5.7, 5.8).

    The computed score is capped at :data:`~edu_it_domain.config.MASTERY_MAX`
    and floored at :data:`~edu_it_domain.config.MASTERY_MIN` (Req 5.1, 5.8); the
    new :class:`MasteryState` is derived from the capped score and the
    Mastered-gate conditions (``review_passed`` and the ``lab_check`` path for
    this ``mastery``'s concept/learner) (Req 5.2-5.6). Exactly one
    :class:`~edu_it_domain.learning.models.MasteryEvent` is appended recording
    the *effective* delta actually applied (the capped/floored score minus the
    prior score), the ``source``, and the instant from the injected ``clock``
    (Req 5.7).

    A new frozen :class:`~edu_it_domain.learning.models.Mastery` is returned; the
    input ``mastery`` is left unchanged.
    """
    new_score = min(MASTERY_MAX, max(MASTERY_MIN, mastery.score + delta))
    effective_delta = new_score - mastery.score

    new_state = derive_state(
        new_score,
        encountered=True,
        review_passed=review_passed,
        lab_check=lab_check,
        concept_id=mastery.concept_id,
        user_id=mastery.user_id,
    )

    event = MasteryEvent(
        delta=effective_delta,
        source=source,
        occurred_at=clock.now(),
    )
    return Mastery(
        user_id=mastery.user_id,
        concept_id=mastery.concept_id,
        score=new_score,
        state=new_state,
        events=_append_event(mastery, event),
    )


def apply_decay(
    mastery: Mastery,
    days_overdue: int,
    *,
    clock: Clock,
    source: str = DECAY_SOURCE,
    lab_check: LabCheck | None = None,
) -> Mastery:
    """Apply spaced-review decay to ``mastery`` and return the new value (Req 5.9-5.11, 8.2, 8.5).

    Mastery is lowered by :data:`~edu_it_domain.config.DECAY_RATE_PER_DAY` (-1)
    for each complete overdue day in ``days_overdue`` and floored at
    :data:`~edu_it_domain.config.DECAY_FLOOR_ONCE_PRACTICED` (30), so decay never
    drops a once-Practiced concept below that floor nor below the Practiced band
    (Req 5.11, 8.2). The resulting state steps at most one band down as a
    threshold is crossed (Mastered->Proficient below the Mastered threshold,
    Proficient->Practiced below the Proficient threshold) (Req 5.9, 5.10, 8.5).

    A decayed concept never re-satisfies the Mastered gate purely by sitting
    still, so the state is derived with the review/lab gate held off — the best a
    decayed concept can be is :attr:`~MasteryState.PROFICIENT`. Exactly one
    :class:`~edu_it_domain.learning.models.MasteryEvent` is appended recording
    the effective reduction (0 when ``days_overdue`` is 0), ``source``, and the
    instant from the injected ``clock`` (Req 5.7). A new frozen
    :class:`~edu_it_domain.learning.models.Mastery` is returned; the input is
    left unchanged.

    ``lab_check`` is accepted for signature symmetry with :func:`apply_delta`;
    decay's resulting state is computed without the Mastered gate, so the port is
    not consulted here.
    """
    del lab_check  # Decay never re-opens the Mastered gate; see docstring.

    reduction = DECAY_RATE_PER_DAY * days_overdue
    new_score = max(DECAY_FLOOR_ONCE_PRACTICED, mastery.score + reduction)
    effective_delta = new_score - mastery.score

    # Derive the band from the decayed score with the Mastered gate held off
    # (review_passed=False), so a once-Mastered concept lands in Proficient and
    # never below Practiced (the floor keeps the score at or above Practiced).
    new_state = derive_state(new_score, encountered=True, review_passed=False)

    event = MasteryEvent(
        delta=effective_delta,
        source=source,
        occurred_at=clock.now(),
    )
    return Mastery(
        user_id=mastery.user_id,
        concept_id=mastery.concept_id,
        score=new_score,
        state=new_state,
        events=_append_event(mastery, event),
    )
