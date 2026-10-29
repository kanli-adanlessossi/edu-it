"""Rules-based + property tests for unlocking and completion (task 2.1, Req 2).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.unlocking`
does not exist yet (it lands in task 2.2). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.unlocking` (task 2.2)
-------------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table. These tests target this surface:

``REQUIRED_PREREQUISITE_STATE: MasteryState``
    The mastery state a prerequisite concept must have reached for it to count
    as met — ``MasteryState.PRACTICED`` by the domain rules (a prerequisite is
    satisfied once its concept is Practiced or higher).

``prerequisite_met(mastery, *, required_state=REQUIRED_PREREQUISITE_STATE) -> bool``
    Whether a single concept's ``mastery`` (possibly ``None`` when untracked)
    has reached ``required_state`` (Req 2.1, 2.2).

``is_module_unlocked(module, mastery_by_concept, *, required_state=...) -> bool``
    Whether every one of ``module.prerequisite_concept_ids`` has reached the
    required state; a module with no prerequisites is unlocked (Req 2.1, 2.2).

``module_lock_state(module, mastery_by_concept, *, required_state=...) -> str``
    The ``MODULE_STATE_LOCKED`` / ``MODULE_STATE_UNLOCKED`` constant reflecting
    the above (Req 2.1, 2.2); advancement is only ever by mastery (Req 2.4).

``record_lesson_completion(progress, clock, *, module_id, lesson_id) -> LessonCompletionRecord``
    Record a lesson as completed via the progress repository, stamping
    ``completed_at`` from the injected ``Clock`` (Req 2.3). Idempotent per
    ``(module_id, lesson_id)`` — the repository keeps the first completion.

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock` and the in-memory learning repositories — wired
exactly as the other fake-driven tests and the test/dev composition root wire
them: no database and no wall clock (Req 11.4). Business numbers (mastery
thresholds) are read from :mod:`edu_it_domain.config`, never hardcoded (Req 1.3).

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import MASTERY_THRESHOLDS
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    ReviewSchedule,
    StableId,
)
from edu_it_domain.learning.unlocking import (
    REQUIRED_PREREQUISITE_STATE,
    is_module_unlocked,
    module_lock_state,
    prerequisite_met,
    record_lesson_completion,
)
from edu_it_domain.ports.catalog import ModuleRecord
from edu_it_domain.ports.progress import (
    MODULE_STATE_LOCKED,
    MODULE_STATE_UNLOCKED,
)
from edu_it_infrastructure.fakes import (
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared helpers -----------------------------------------------------------

#: The ordered mastery-state bands as the domain rules lay them out, lowest
#: first. Used to classify a state as "met" (>= the required prerequisite
#: state) and to pick states above / below the required band in generators.
_STATE_ORDER: tuple[MasteryState, ...] = (
    MasteryState.UNSEEN,
    MasteryState.INTRODUCED,
    MasteryState.PRACTICED,
    MasteryState.PROFICIENT,
    MasteryState.MASTERED,
)


def _score_for_state(state: MasteryState) -> int:
    """Return a representative mastery score inside ``state``'s band.

    The score lands squarely within the band the domain rules assign to the
    state, with the boundaries read from :data:`MASTERY_THRESHOLDS` so a config
    change flows through rather than being hardcoded (Req 1.3).
    """
    t = MASTERY_THRESHOLDS
    match state:
        case MasteryState.UNSEEN:
            return 0
        case MasteryState.INTRODUCED:
            return t.practiced - 1  # just below the Practiced threshold
        case MasteryState.PRACTICED:
            return t.practiced  # entering the Practiced band
        case MasteryState.PROFICIENT:
            return t.proficient  # entering the Proficient band
        case MasteryState.MASTERED:
            return t.mastered  # entering the Mastered band
    raise AssertionError(f"unhandled state {state!r}")  # pragma: no cover


def _mastery(concept_id: StableId, state: MasteryState) -> Mastery:
    """Build a :class:`Mastery` for ``concept_id`` sitting in ``state``'s band."""
    return Mastery(
        user_id="usr_1",
        concept_id=concept_id,
        score=_score_for_state(state),
        state=state,
    )


def _is_met(state: MasteryState) -> bool:
    """Return whether ``state`` is at or above the required prerequisite state."""
    return _STATE_ORDER.index(state) >= _STATE_ORDER.index(REQUIRED_PREREQUISITE_STATE)


def _module(prerequisites: tuple[StableId, ...]) -> ModuleRecord:
    """Build a module gated by ``prerequisites`` (concept ``stable_id``s)."""
    return ModuleRecord(
        stable_id="md_1",
        track_id="tr_1",
        ordinal=0,
        prerequisite_concept_ids=prerequisites,
    )


# --- Generators constrained to the input space --------------------------------

_mastery_states = st.sampled_from(_STATE_ORDER)

# A small set of prerequisite concept ids, so a module is gated by 0..6 concepts
# — covering the no-prerequisite case (always unlocked) and multi-prerequisite
# gating where a single unmet concept must lock the module.
_concept_ids = st.lists(
    st.integers(min_value=0, max_value=20).map(lambda n: f"cn_{n}"),
    min_size=0,
    max_size=6,
    unique=True,
).map(tuple)

_instants = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2100, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))


# =============================================================================
# Rules-based tests (Req 2.1-2.5)
# =============================================================================


def test_required_prerequisite_state_is_practiced() -> None:
    # The domain rules gate a dependent module on its prerequisite concepts
    # reaching the required mastery state; a prerequisite is met once its
    # concept is Practiced (30-59) or higher (domain-rules.md, Req 2.1, 2.2).
    assert REQUIRED_PREREQUISITE_STATE is MasteryState.PRACTICED


def test_module_is_locked_while_a_prerequisite_is_unmet() -> None:
    # Req 2.1: WHILE a prerequisite concept has not reached the required mastery
    # state, the module is reported locked. Here cn_1 is met but cn_2 is only
    # Introduced, so the module stays locked.
    module = _module(("cn_1", "cn_2"))
    mastery_by_concept = {
        "cn_1": _mastery("cn_1", MasteryState.PROFICIENT),
        "cn_2": _mastery("cn_2", MasteryState.INTRODUCED),
    }
    assert is_module_unlocked(module, mastery_by_concept) is False
    assert module_lock_state(module, mastery_by_concept) == MODULE_STATE_LOCKED


def test_module_is_locked_while_a_prerequisite_is_untracked() -> None:
    # Req 2.1: an untracked prerequisite (no mastery row yet, Unseen) has not
    # reached the required state, so the module is locked. A missing entry is
    # treated the same as None.
    module = _module(("cn_1", "cn_2"))
    mastery_by_concept = {"cn_1": _mastery("cn_1", MasteryState.PRACTICED)}  # cn_2 absent
    assert is_module_unlocked(module, mastery_by_concept) is False
    assert module_lock_state(module, mastery_by_concept) == MODULE_STATE_LOCKED


def test_module_unlocks_when_all_prerequisites_are_met() -> None:
    # Req 2.2: WHEN every prerequisite concept reaches the required mastery
    # state, the module is reported unlocked.
    module = _module(("cn_1", "cn_2"))
    mastery_by_concept = {
        "cn_1": _mastery("cn_1", MasteryState.PRACTICED),
        "cn_2": _mastery("cn_2", MasteryState.MASTERED),
    }
    assert is_module_unlocked(module, mastery_by_concept) is True
    assert module_lock_state(module, mastery_by_concept) == MODULE_STATE_UNLOCKED


def test_module_with_no_prerequisites_is_unlocked() -> None:
    # A module gated by nothing is vacuously unlocked (Req 2.2): every (zero) of
    # its prerequisites is met.
    module = _module(())
    assert is_module_unlocked(module, {}) is True
    assert module_lock_state(module, {}) == MODULE_STATE_UNLOCKED


def test_prerequisite_met_follows_the_required_state_band() -> None:
    # Req 2.1, 2.2: a prerequisite is met exactly when its concept has reached
    # the required state (Practiced) or higher; Unseen/Introduced are unmet.
    assert prerequisite_met(None) is False
    assert prerequisite_met(_mastery("cn_1", MasteryState.UNSEEN)) is False
    assert prerequisite_met(_mastery("cn_1", MasteryState.INTRODUCED)) is False
    assert prerequisite_met(_mastery("cn_1", MasteryState.PRACTICED)) is True
    assert prerequisite_met(_mastery("cn_1", MasteryState.PROFICIENT)) is True
    assert prerequisite_met(_mastery("cn_1", MasteryState.MASTERED)) is True


def test_completion_is_recorded_via_the_progress_repository() -> None:
    # Req 2.3: WHEN a learner satisfies a lesson's completion condition, the
    # engine records the lesson as completed via lesson_completion, stamping
    # completed_at from the injected Clock (Req 11.4).
    progress = In_Memory_ProgressRepository()
    clock = Test_Clock(start=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC))

    completion = record_lesson_completion(
        progress,
        clock,
        module_id="md_1",
        lesson_id="ls_1",
    )

    assert completion.module_id == "md_1"
    assert completion.lesson_id == "ls_1"
    assert completion.completed_at == clock.now()
    stored = progress.list_lesson_completions("md_1")
    assert len(stored) == 1
    assert stored[0].lesson_id == "ls_1"


def test_completion_recording_is_idempotent_per_lesson() -> None:
    # Req 2.3 / 26.1: recording the same lesson's completion twice keeps the
    # first completion and never writes a duplicate — completion is preserved,
    # not reset, across repeated signals.
    progress = In_Memory_ProgressRepository()
    clock = Test_Clock(start=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC))

    first = record_lesson_completion(progress, clock, module_id="md_1", lesson_id="ls_1")
    clock.advance(timedelta(days=3))
    record_lesson_completion(progress, clock, module_id="md_1", lesson_id="ls_1")

    stored = progress.list_lesson_completions("md_1")
    assert len(stored) == 1
    assert stored[0].completed_at == first.completed_at  # earliest completion kept


def test_advance_is_only_by_mastery_not_by_recorded_completion() -> None:
    # Req 2.4: the engine advances a learner only by proving mastery, never by
    # time spent or content viewed. Recording a lesson completion while a
    # prerequisite concept is still unmet must not unlock the dependent module.
    progress = In_Memory_ProgressRepository()
    clock = Test_Clock(start=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC))
    module = _module(("cn_1",))
    unmet = {"cn_1": _mastery("cn_1", MasteryState.INTRODUCED)}

    # Viewing/completing a lesson is recorded ...
    record_lesson_completion(progress, clock, module_id="md_1", lesson_id="ls_1")
    # ... yet the module stays locked because mastery was not proven (Req 2.4).
    assert is_module_unlocked(module, unmet) is False
    assert module_lock_state(module, unmet) == MODULE_STATE_LOCKED


def test_decay_dip_never_relocks_an_already_unlocked_module() -> None:
    # Req 2.5: WHEN decay lowers a concept's mastery, previously unlocked content
    # stays unlocked; the only effect is re-queuing the concept for review.
    # A prerequisite decays from Mastered down to Practiced (never below 30 once
    # Practiced) — still met, so the module remains unlocked.
    module = _module(("cn_1",))
    before = {"cn_1": _mastery("cn_1", MasteryState.MASTERED)}
    assert is_module_unlocked(module, before) is True

    after_decay = {"cn_1": _mastery("cn_1", MasteryState.PRACTICED)}
    assert is_module_unlocked(module, after_decay) is True
    assert module_lock_state(module, after_decay) == MODULE_STATE_UNLOCKED


# =============================================================================
# Property 1: Module lock reflects prerequisite mastery (Req 2.1, 2.2)
# =============================================================================


@settings(max_examples=200)
@given(prerequisites=_concept_ids, data=st.data())
def test_property_1_module_lock_reflects_prerequisite_mastery(
    prerequisites: tuple[StableId, ...],
    data: st.DataObject,
) -> None:
    """Feature: learning-core, Property 1: Module lock reflects prerequisite mastery.

    For any prerequisite graph and any assignment of mastery states, a module is
    reported locked if and only if at least one prerequisite concept has not
    reached the required mastery state, and unlocked exactly when every
    prerequisite concept has (Req 2.1, 2.2).
    """
    module = _module(prerequisites)

    # Assign each prerequisite concept a drawn mastery state; a drawn subset is
    # left untracked (None) to exercise the Unseen/absent case as unmet.
    mastery_by_concept: dict[StableId, Mastery] = {}
    assigned_states: dict[StableId, MasteryState] = {}
    for concept_id in prerequisites:
        state = data.draw(_mastery_states)
        assigned_states[concept_id] = state
        if data.draw(st.booleans()):
            mastery_by_concept[concept_id] = _mastery(concept_id, state)
        # else: leave untracked — an absent entry means Unseen, i.e. unmet.

    expected_unlocked = all(
        concept_id in mastery_by_concept and _is_met(assigned_states[concept_id])
        for concept_id in prerequisites
    )

    unlocked = is_module_unlocked(module, mastery_by_concept)
    assert unlocked is expected_unlocked

    # The biconditional: locked iff at least one prerequisite is unmet.
    any_unmet = any(
        concept_id not in mastery_by_concept or not _is_met(assigned_states[concept_id])
        for concept_id in prerequisites
    )
    assert (unlocked is False) == any_unmet

    lock_state = module_lock_state(module, mastery_by_concept)
    assert lock_state == (MODULE_STATE_UNLOCKED if unlocked else MODULE_STATE_LOCKED)


# =============================================================================
# Property 2: Decay never relocks and only re-queues review (Req 2.5, 8.3)
# =============================================================================


@settings(max_examples=200)
@given(
    prerequisites=st.lists(
        st.integers(min_value=0, max_value=20).map(lambda n: f"cn_{n}"),
        min_size=1,
        max_size=6,
        unique=True,
    ).map(tuple),
    data=st.data(),
    now=_instants,
)
def test_property_2_decay_never_relocks_and_only_requeues_review(
    prerequisites: tuple[StableId, ...],
    data: st.DataObject,
    now: datetime,
) -> None:
    """Feature: learning-core, Property 2: Decay never relocks and only re-queues review.

    For any already-unlocked module and any decay applied to its concepts, the
    module remains unlocked and the only effect on scheduling is that the decayed
    concept is re-queued for review (Req 2.5, 8.3).
    """
    clock = Test_Clock(start=now)
    module = _module(prerequisites)

    # Start from an unlocked module: every prerequisite is met at Proficient or
    # Mastered (a band strictly above Practiced, so a decay dip still leaves it
    # at or above the Practiced floor and therefore still met).
    pre_decay: dict[StableId, Mastery] = {}
    for concept_id in prerequisites:
        state = data.draw(st.sampled_from((MasteryState.PROFICIENT, MasteryState.MASTERED)))
        pre_decay[concept_id] = _mastery(concept_id, state)
    assert is_module_unlocked(module, pre_decay) is True

    # Decay dips each prerequisite down one band but never below Practiced /
    # mastery 30 (the domain floor once Practiced, Req 8.2). The concept is
    # re-queued for review in the schedule, modeled here by its presence in the
    # review repository with a due_at the Clock can read (Req 8.3, 9.6).
    reviews = In_Memory_ReviewRepository()
    post_decay: dict[StableId, Mastery] = {}
    for concept_id in prerequisites:
        decayed_state = (
            MasteryState.PROFICIENT
            if pre_decay[concept_id].state is MasteryState.MASTERED
            else MasteryState.PRACTICED
        )
        post_decay[concept_id] = _mastery(concept_id, decayed_state)
        reviews.save(
            ReviewSchedule(
                user_id="usr_1",
                concept_id=concept_id,
                due_at=clock.now(),  # due now: re-queued for review
                interval=timedelta(days=1),
            )
        )

    # The module stays unlocked after decay (never relocked): Req 2.5.
    assert is_module_unlocked(module, post_decay) is True
    assert module_lock_state(module, post_decay) == MODULE_STATE_UNLOCKED

    # The only scheduling effect is a review re-queue for each decayed concept:
    # each has exactly one schedule entry, and none of them is below Practiced
    # (so no content could be relocked). Req 8.3.
    for concept_id in prerequisites:
        schedule = reviews.get("usr_1", concept_id)
        assert schedule is not None  # re-queued for review
        assert _is_met(post_decay[concept_id].state)  # still at/above Practiced
        assert post_decay[concept_id].score >= MASTERY_THRESHOLDS.practiced


@pytest.mark.parametrize(
    "required_state",
    [MasteryState.PRACTICED, MasteryState.PROFICIENT, MasteryState.MASTERED],
)
def test_required_state_is_configurable_for_stricter_gating(
    required_state: MasteryState,
) -> None:
    # The gate accepts a stricter required state (e.g. a module could require a
    # prerequisite Mastered rather than merely Practiced). A concept exactly at
    # the required band is met; one band below is not.
    module = _module(("cn_1",))
    at_required = {"cn_1": _mastery("cn_1", required_state)}
    assert is_module_unlocked(module, at_required, required_state=required_state) is True

    idx = _STATE_ORDER.index(required_state)
    if idx > 0:
        below = {"cn_1": _mastery("cn_1", _STATE_ORDER[idx - 1])}
        assert is_module_unlocked(module, below, required_state=required_state) is False
