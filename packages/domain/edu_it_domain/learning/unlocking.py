"""Module unlocking and lesson-completion decisions (Req 2).

This pure, framework-free domain service computes a module's lock/unlock state
from the mastery of its prerequisite concepts and records a lesson completion
through the progress repository, stamping the completion instant from the
injected ``Clock`` (Req 1.1, 1.3). Like the rest of ``edu_it_domain`` it imports
no web or API framework (Req 1.8, ``.kiro/steering/structure.md``) and reads time
only through the injected ``Clock`` (Req 1.3).

The domain rules it encodes (``.kiro/steering/domain-rules.md``, Req 2):

- **Lock reflects prerequisite mastery (Req 2.1, 2.2).** A module is unlocked
  exactly when *every* one of its prerequisite concepts has reached the required
  mastery state — :data:`REQUIRED_PREREQUISITE_STATE`, i.e. ``Practiced`` or
  higher. While any prerequisite is unmet (untracked, Unseen, or merely
  Introduced) the module is locked. A module with no prerequisites is vacuously
  unlocked.
- **Advance only by mastery (Req 2.4).** Lock state is derived solely from
  prerequisite mastery; recording a lesson completion (time spent / content
  viewed) never unlocks a module.
- **Completion recording (Req 2.3, 26.1).** A satisfied lesson is recorded via
  ``lesson_completion`` with ``completed_at`` from the ``Clock``; the recording
  is idempotent per ``(module_id, lesson_id)`` — the earliest completion is
  kept and no duplicate is written.
- **Decay never relocks (Req 2.5).** Because lock state is a pure function of
  the *current* prerequisite mastery, a decay dip that leaves a prerequisite at
  or above Practiced (the floor once Practiced, Req 8.2) leaves the module
  unlocked; decay's only effect on scheduling is to re-queue the concept for
  review, which is the review service's concern, not this one's.

The mastery-state band ordering used to decide "met" is derived from the
declaration order of :class:`~edu_it_domain.learning.models.MasteryState`
(Unseen < Introduced < Practiced < Proficient < Mastered), so a concept is met
when its state is at or above the required state in that order. No business
number is hardcoded here — the thresholds that place a score in a band live in
:mod:`edu_it_domain.config` and are applied by the mastery service that derives
:attr:`~edu_it_domain.learning.models.Mastery.state`; this service compares the
already-derived states.
"""

from __future__ import annotations

from typing import Final

from edu_it_domain.learning.models import Mastery, MasteryState, StableId
from edu_it_domain.ports.catalog import ModuleRecord
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.progress import (
    MODULE_STATE_LOCKED,
    MODULE_STATE_UNLOCKED,
    LessonCompletionRecord,
    ModuleProgressRecord,
    ProgressRepository,
)

__all__ = [
    "REQUIRED_PREREQUISITE_STATE",
    "is_module_unlocked",
    "module_lock_state",
    "prerequisite_met",
    "record_lesson_completion",
    "record_tested_out",
]

#: The ordered mastery-state bands, lowest first, as the domain rules lay them
#: out. A concept counts as having "reached" a state when its state is at or
#: above that state in this order. Derived from the declaration order of
#: :class:`~edu_it_domain.learning.models.MasteryState` so it stays in lockstep
#: with the enum rather than duplicating the ordering.
_STATE_ORDER: Final[tuple[MasteryState, ...]] = tuple(MasteryState)

#: The mastery state a prerequisite concept must have reached for it to count as
#: met: ``Practiced`` (the Practiced band or higher). A dependent module is
#: gated on each of its prerequisite concepts reaching this state (Req 2.1, 2.2;
#: ``.kiro/steering/domain-rules.md``).
REQUIRED_PREREQUISITE_STATE: Final[MasteryState] = MasteryState.PRACTICED


def _has_reached(state: MasteryState, required_state: MasteryState) -> bool:
    """Return whether ``state`` is at or above ``required_state`` in the band order."""
    return _STATE_ORDER.index(state) >= _STATE_ORDER.index(required_state)


def prerequisite_met(
    mastery: Mastery | None,
    *,
    required_state: MasteryState = REQUIRED_PREREQUISITE_STATE,
) -> bool:
    """Return whether a single prerequisite concept's ``mastery`` is met (Req 2.1, 2.2).

    A prerequisite is met exactly when its concept has reached ``required_state``
    (``Practiced`` by default) or higher. An untracked concept — ``mastery`` of
    ``None`` — has not reached any state and is therefore unmet, as is a concept
    still at ``Unseen`` or ``Introduced``.
    """
    if mastery is None:
        return False
    return _has_reached(mastery.state, required_state)


def is_module_unlocked(
    module: ModuleRecord,
    mastery_by_concept: dict[StableId, Mastery],
    *,
    required_state: MasteryState = REQUIRED_PREREQUISITE_STATE,
) -> bool:
    """Return whether ``module`` is unlocked for the given prerequisite mastery (Req 2.1, 2.2).

    The module is unlocked exactly when *every* concept in
    :attr:`~edu_it_domain.ports.catalog.ModuleRecord.prerequisite_concept_ids`
    has reached ``required_state``; a prerequisite absent from
    ``mastery_by_concept`` is treated as untracked (``None``) and therefore
    unmet. A module with no prerequisites is vacuously unlocked.

    Lock state is a pure function of the supplied mastery, so it never depends on
    recorded completions (advancement is only by mastery, Req 2.4) and a decay
    dip that leaves every prerequisite at or above the required state leaves the
    module unlocked (Req 2.5).
    """
    return all(
        prerequisite_met(mastery_by_concept.get(concept_id), required_state=required_state)
        for concept_id in module.prerequisite_concept_ids
    )


def module_lock_state(
    module: ModuleRecord,
    mastery_by_concept: dict[StableId, Mastery],
    *,
    required_state: MasteryState = REQUIRED_PREREQUISITE_STATE,
) -> str:
    """Return ``module``'s lock-state constant for the given prerequisite mastery (Req 2.1, 2.2).

    Returns :data:`~edu_it_domain.ports.progress.MODULE_STATE_UNLOCKED` when every
    prerequisite is met and :data:`~edu_it_domain.ports.progress.MODULE_STATE_LOCKED`
    otherwise, mirroring :func:`is_module_unlocked`.
    """
    unlocked = is_module_unlocked(module, mastery_by_concept, required_state=required_state)
    return MODULE_STATE_UNLOCKED if unlocked else MODULE_STATE_LOCKED


def record_lesson_completion(
    progress: ProgressRepository,
    clock: Clock,
    *,
    module_id: StableId,
    lesson_id: StableId,
) -> LessonCompletionRecord:
    """Record a lesson as completed via the progress repository (Req 2.3, 26.1).

    Builds a :class:`~edu_it_domain.ports.progress.LessonCompletionRecord` with
    ``completed_at`` stamped from the injected ``clock`` and persists it through
    ``progress``. Recording is idempotent per ``(module_id, lesson_id)``: the
    repository keeps the first completion and writes no duplicate, so the
    returned record reflects the completion the repository holds for the lesson —
    the earliest one when the lesson was already completed.

    Recording a completion never changes a module's lock state: advancement is
    only by proving mastery (Req 2.4).
    """
    completion = LessonCompletionRecord(
        module_id=module_id,
        lesson_id=lesson_id,
        completed_at=clock.now(),
    )
    progress.add_lesson_completion(completion)
    for stored in progress.list_lesson_completions(module_id):
        if stored.lesson_id == lesson_id:
            return stored
    return completion


def record_tested_out(
    progress: ProgressRepository,
    *,
    enrollment_id: StableId,
    module_id: StableId,
) -> ModuleProgressRecord:
    """Record that a learner tested out of a module at placement (Req 21.3).

    Upserts the ``module_progress`` row for ``(enrollment_id, module_id)`` with
    ``tested_out`` set to ``True`` through the progress repository. An existing
    row's ``state`` is preserved (testing out does not itself change lock state;
    advancement remains derived from mastery, Req 2.4); with no existing row the
    module starts as :data:`~edu_it_domain.ports.progress.MODULE_STATE_UNLOCKED`.
    Recording is idempotent. Grading of the placement itself is out of scope
    (Req 21.2); callers decide that the learner tested out.
    """
    existing = progress.get_module_progress(enrollment_id, module_id)
    record = ModuleProgressRecord(
        enrollment_id=enrollment_id,
        module_id=module_id,
        state=existing.state if existing is not None else MODULE_STATE_UNLOCKED,
        tested_out=True,
    )
    progress.upsert_module_progress(record)
    return record
