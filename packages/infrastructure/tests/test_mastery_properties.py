"""Rules-based + property tests for mastery (task 2.3, Req 5, 8.2, 8.5).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.mastery`
does not exist yet (it lands in task 2.4). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.mastery` (task 2.4)
-----------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Apply mastery deltas; derive states/thresholds; cap
0-100; append ``mastery_event``; decay state transitions; floor-30-once-
Practiced"). Business numbers (the 30/60/85 thresholds, the 0/100 bounds, the
30 floor, the -1/day decay rate) are read from :mod:`edu_it_domain.config`,
never hardcoded (Req 1.3). These tests target this surface:

``derive_state(score, *, encountered=True, ever_practiced=False, review_passed=False,
lab_check=None, concept_id=None, user_id=None) -> MasteryState``
    The pure state-band classifier (Req 5.2, 5.3, 5.5, 5.6). Maps an integer
    ``score`` (and the Mastered-gate conditions) to a
    :class:`~edu_it_domain.learning.models.MasteryState`:

    - ``score`` below the Practiced threshold → ``INTRODUCED`` when the concept
      has been encountered, else ``UNSEEN`` (Req 5.2).
    - Practiced band (threshold .. Proficient-1) → ``PRACTICED`` (Req 5.3).
    - Proficient band (Proficient .. Mastered-1) → ``PROFICIENT`` (Req 5.5).
    - At/above the Mastered threshold → ``MASTERED`` only when ``review_passed``
      is true **and** the lab-check condition holds; otherwise the numeric
      score alone leaves the state at ``PROFICIENT`` (Req 5.6). The lab-check
      condition holds when no ``lab_check`` is supplied, or when the supplied
      :class:`~edu_it_domain.ports.lab_check.LabCheck` reports the concept is
      not covered by a lab, or — when it is covered — that the learner has
      passed it (Req 1.4, 5.6). ``ever_practiced`` reflects whether the concept
      has ever reached Practiced, which keeps a below-threshold score as
      ``INTRODUCED``/``UNSEEN`` vs the decay transitions handled by
      :func:`apply_decay`.

``apply_delta(mastery, delta, *, clock, source, review_passed=False, lab_check=None) -> Mastery``
    Apply a signed ``delta`` to ``mastery.score``, capping at ``MASTERY_MAX``
    (100) and flooring at ``MASTERY_MIN`` (0) (Req 5.1, 5.8); derive the new
    state from the capped score and the Mastered-gate conditions (Req 5.2-5.6);
    and append **exactly one** :class:`~edu_it_domain.learning.models.MasteryEvent`
    recording the *effective* delta applied, the ``source``, and the occurrence
    instant read from the injected ``Clock`` (Req 5.7). Returns a new
    :class:`~edu_it_domain.learning.models.Mastery`; the input is left unchanged
    (the domain types are frozen). The lab-check path for the Mastered gate is
    consulted via ``lab_check`` + ``mastery.concept_id``/``user_id``.

``apply_decay(mastery, days_overdue, *, clock, source=..., lab_check=None) -> Mastery``
    Apply decay of ``DECAY_RATE_PER_DAY`` (-1) per complete overdue day to a
    concept that has reached Practiced, flooring the score at
    ``DECAY_FLOOR_ONCE_PRACTICED`` (30) so decay never drops a once-Practiced
    concept below 30 nor below the Practiced band (Req 5.9, 5.10, 5.11, 8.2,
    8.5); transition the state exactly one band down as the score crosses a
    threshold (Mastered→Proficient below 85, Proficient→Practiced below 60),
    never below Practiced; and append exactly one ``mastery_event`` for the
    applied reduction (Req 5.7). A once-Mastered concept never re-satisfies the
    Mastered gate purely by sitting still, so decay's resulting state is derived
    without the review/lab gate (a decayed concept is at most Proficient).

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock`, the in-memory :class:`In_Memory_MasteryRepository`,
and the configurable :class:`Fake_LabCheck` — wired exactly as the other
fake-driven tests wire them: no database and no wall clock (Req 11.4). Every
mastery write is persisted through the repository so the append-only
``mastery_event`` invariant (Req 5.7) is exercised end to end.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import (
    DECAY_FLOOR_ONCE_PRACTICED,
    DECAY_RATE_PER_DAY,
    MASTERY_MAX,
    MASTERY_MIN,
    MASTERY_THRESHOLDS,
)
from edu_it_domain.learning.mastery import (
    apply_decay,
    apply_delta,
    derive_state,
)
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    StableId,
)
from edu_it_infrastructure.fakes import (
    Fake_LabCheck,
    In_Memory_MasteryRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_USER: StableId = "usr_1"
_CONCEPT: StableId = "cn_1"
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _mastery(
    score: int,
    state: MasteryState,
    *,
    concept_id: StableId = _CONCEPT,
    user_id: StableId = _USER,
) -> Mastery:
    """Build a freshly-tracked :class:`Mastery` at ``score`` in ``state``.

    The event history starts empty; the services under test append to it.
    """
    return Mastery(user_id=user_id, concept_id=concept_id, score=score, state=state)


def _expected_band(score: int) -> MasteryState:
    """Return the numeric band an ``score`` falls in, ignoring the Mastered gate.

    The boundaries are read from :data:`MASTERY_THRESHOLDS` so a config change
    flows through rather than being hardcoded (Req 1.3). The band is the state a
    score maps to *before* the review/lab gate is applied — a score at or above
    the Mastered threshold whose gate is unmet stays Proficient (Req 5.6).
    """
    t = MASTERY_THRESHOLDS
    if score < t.practiced:
        return MasteryState.INTRODUCED
    if score < t.proficient:
        return MasteryState.PRACTICED
    if score < t.mastered:
        return MasteryState.PROFICIENT
    return MasteryState.PROFICIENT  # numeric Mastered band, gate still pending


# --- Generators constrained to the input space -------------------------------

#: Mastery scores spanning the full 0-100 scale plus the exact band boundaries.
_scores = st.integers(min_value=MASTERY_MIN, max_value=MASTERY_MAX)

#: Deltas that deliberately overshoot 100 and undershoot 0 so the cap/floor are
#: exercised (Req 5.8) alongside ordinary in-range moves.
_deltas = st.integers(min_value=-200, max_value=200)

_instants = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2100, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))

#: Overdue-day counts including 0 (not overdue) and exact day boundaries.
_overdue_days = st.integers(min_value=0, max_value=365)


# =============================================================================
# Rules-based tests — state bands (Req 5.2, 5.3, 5.5)
# =============================================================================


def test_below_practiced_threshold_is_introduced_on_first_encounter() -> None:
    # Req 5.2: WHEN a concept is first encountered AND mastery is below 30, the
    # state is Introduced. The boundary is read from config (practiced - 1).
    assert derive_state(0) is MasteryState.INTRODUCED
    assert derive_state(MASTERY_THRESHOLDS.practiced - 1) is MasteryState.INTRODUCED


def test_zero_unseen_concept_is_unseen_until_encountered() -> None:
    # A concept that has not been encountered is Unseen, not Introduced; the
    # Introduced band is for an encountered concept still below 30 (Req 5.2).
    # ``ever_practiced=False`` + an explicit "not encountered" signal keeps it
    # Unseen. The implementation models "encountered" via encountering it at all
    # — a score of 0 with no encounter is Unseen.
    assert derive_state(0, encountered=False) is MasteryState.UNSEEN
    assert derive_state(0, encountered=True) is MasteryState.INTRODUCED


def test_practiced_band_is_practiced_at_and_above_30() -> None:
    # Req 5.3: WHEN mastery enters 30-59 the state is Practiced. 30 enters the
    # band (inclusive lower bound) and 59 is the top of the band.
    assert derive_state(MASTERY_THRESHOLDS.practiced) is MasteryState.PRACTICED
    assert derive_state(MASTERY_THRESHOLDS.proficient - 1) is MasteryState.PRACTICED


def test_proficient_band_is_proficient_between_60_and_84() -> None:
    # Req 5.5: WHEN mastery enters 60-84 the state is Proficient.
    assert derive_state(MASTERY_THRESHOLDS.proficient) is MasteryState.PROFICIENT
    assert derive_state(MASTERY_THRESHOLDS.mastered - 1) is MasteryState.PROFICIENT


# =============================================================================
# Rules-based tests — the Mastered gate incl. the lab-check path (Req 5.6, 1.4)
# =============================================================================


def test_mastered_numeric_threshold_alone_is_not_enough() -> None:
    # Req 5.6: >= 85 alone does not reach Mastered; a passed review is also
    # required. Without a passed review the state stays Proficient even at 100.
    assert derive_state(MASTERY_THRESHOLDS.mastered, review_passed=False) is (
        MasteryState.PROFICIENT
    )
    assert derive_state(MASTERY_MAX, review_passed=False) is MasteryState.PROFICIENT


def test_mastered_reached_with_score_and_passed_review_when_no_lab_covers() -> None:
    # Req 5.6: >= 85 + passed review reaches Mastered when no lab covers the
    # concept (the lab-check condition is vacuously satisfied, Req 1.4).
    lab = Fake_LabCheck()  # covers nothing
    state = derive_state(
        MASTERY_THRESHOLDS.mastered,
        review_passed=True,
        lab_check=lab,
        concept_id=_CONCEPT,
        user_id=_USER,
    )
    assert state is MasteryState.MASTERED


def test_mastered_requires_passed_lab_check_when_a_lab_covers_the_concept() -> None:
    # Req 5.6 / 1.4: where a lab covers the concept, the Mastered gate also needs
    # the lab check to have passed. Score >= 85 and a passed review are not
    # enough while the lab check is unpassed.
    lab = Fake_LabCheck(covered={_CONCEPT})  # covered but not passed
    unmet = derive_state(
        MASTERY_MAX,
        review_passed=True,
        lab_check=lab,
        concept_id=_CONCEPT,
        user_id=_USER,
    )
    assert unmet is MasteryState.PROFICIENT

    lab.set_passed(_CONCEPT, _USER, passed=True)
    met = derive_state(
        MASTERY_MAX,
        review_passed=True,
        lab_check=lab,
        concept_id=_CONCEPT,
        user_id=_USER,
    )
    assert met is MasteryState.MASTERED


# =============================================================================
# Rules-based tests — cap at 100 and floor at 0 (Req 5.1, 5.8)
# =============================================================================


def test_delta_over_100_is_capped_at_the_max() -> None:
    # Req 5.8: a mastery change computing a value above 100 is capped at 100.
    clock = Test_Clock(start=_START)
    before = _mastery(90, MasteryState.PROFICIENT)
    after = apply_delta(before, 50, clock=clock, source="quiz")
    assert after.score == MASTERY_MAX
    # The appended event records the *effective* delta actually applied (100-90).
    assert after.events[-1].delta == MASTERY_MAX - before.score


def test_delta_below_0_is_floored_at_the_min_for_a_never_practiced_concept() -> None:
    # Req 5.8: a mastery change computing a value below 0 is floored at 0. The
    # 30-floor applies only once Practiced (Req 8.2); a concept still Introduced
    # floors at 0.
    clock = Test_Clock(start=_START)
    before = _mastery(10, MasteryState.INTRODUCED)
    after = apply_delta(before, -50, clock=clock, source="decay")
    assert after.score == MASTERY_MIN
    assert after.events[-1].delta == MASTERY_MIN - before.score  # effective delta (negative)


# =============================================================================
# Rules-based tests — floor-30-once-Practiced (Req 5.9, 5.10, 5.11, 8.2)
# =============================================================================


def test_decay_never_drops_a_once_practiced_concept_below_30() -> None:
    # Req 8.2 / 5.11: once a concept has reached Practiced, decay never lowers
    # mastery below the 30 floor and never relocks it to Introduced/Unseen.
    clock = Test_Clock(start=_START)
    before = _mastery(MASTERY_THRESHOLDS.practiced + 5, MasteryState.PRACTICED)
    after = apply_decay(before, days_overdue=100, clock=clock)
    assert after.score == DECAY_FLOOR_ONCE_PRACTICED
    assert after.state is MasteryState.PRACTICED  # never below Practiced


def test_decay_mastered_to_proficient_below_the_mastered_threshold() -> None:
    # Req 5.9 / 8.5: WHILE Mastered, IF decay lowers mastery below 85, the state
    # becomes Proficient (one band down), without dropping below 30.
    clock = Test_Clock(start=_START)
    before = _mastery(MASTERY_THRESHOLDS.mastered, MasteryState.MASTERED)
    after = apply_decay(before, days_overdue=1, clock=clock)  # -1 → 84
    assert after.score == MASTERY_THRESHOLDS.mastered + DECAY_RATE_PER_DAY
    assert after.state is MasteryState.PROFICIENT


def test_decay_proficient_to_practiced_below_the_proficient_threshold() -> None:
    # Req 5.10 / 8.5: WHILE Proficient, IF decay lowers mastery below 60, the
    # state becomes Practiced (one band down), never below Practiced or 30.
    clock = Test_Clock(start=_START)
    before = _mastery(MASTERY_THRESHOLDS.proficient, MasteryState.PROFICIENT)
    after = apply_decay(before, days_overdue=1, clock=clock)  # -1 → 59
    assert after.score == MASTERY_THRESHOLDS.proficient + DECAY_RATE_PER_DAY
    assert after.state is MasteryState.PRACTICED


# =============================================================================
# Rules-based tests — exactly one mastery_event per change (Req 5.7)
# =============================================================================


def test_apply_delta_appends_exactly_one_event_with_clock_instant() -> None:
    # Req 5.7: WHEN any change to mastery is applied, exactly one mastery_event
    # is appended recording the delta, the source, and the Clock instant.
    clock = Test_Clock(start=_START)
    before = _mastery(20, MasteryState.INTRODUCED)
    after = apply_delta(before, 18, clock=clock, source="quiz")
    assert len(after.events) == len(before.events) + 1
    event = after.events[-1]
    assert event.delta == 18
    assert event.source == "quiz"
    assert event.occurred_at == clock.now()


def test_apply_decay_appends_exactly_one_event() -> None:
    # Req 5.7: decay is a mastery change, so it too appends exactly one event
    # recording the reduction applied and the Clock instant.
    clock = Test_Clock(start=_START)
    before = _mastery(MASTERY_THRESHOLDS.proficient + 5, MasteryState.PROFICIENT)
    after = apply_decay(before, days_overdue=3, clock=clock)
    assert len(after.events) == len(before.events) + 1
    event = after.events[-1]
    assert event.delta == DECAY_RATE_PER_DAY * 3
    assert event.occurred_at == clock.now()


def test_each_change_appends_one_event_across_a_sequence() -> None:
    # Req 5.7: a run of N changes appends exactly N events, in order, each
    # stamped at the Clock instant it was applied — the history only grows.
    clock = Test_Clock(start=_START)
    repo = In_Memory_MasteryRepository()
    mastery = _mastery(0, MasteryState.INTRODUCED)
    repo.save(mastery)

    for step, delta in enumerate((18, 18, 9, 18), start=1):
        clock.advance(timedelta(days=1))
        mastery = apply_delta(mastery, delta, clock=clock, source="quiz")
        repo.save(mastery)  # append-only repository accepts the growing history
        assert len(mastery.events) == step

    stored = repo.get(_USER, _CONCEPT)
    assert stored is not None
    assert len(stored.events) == 4


# =============================================================================
# Property 7: Mastery stays within 0-100 (Req 5.1, 5.8)
# =============================================================================


@settings(max_examples=200)
@given(start_score=_scores, deltas=st.lists(_deltas, min_size=1, max_size=20), now=_instants)
def test_property_7_mastery_stays_within_0_100(
    start_score: int,
    deltas: list[int],
    now: datetime,
) -> None:
    """Feature: learning-core, Property 7: Mastery stays within 0-100.

    For any sequence of mastery deltas applied to a concept, the stored mastery
    value is always an integer in the inclusive range 0-100 (over-100 results
    capped at 100, below-0 results floored at 0) (Req 5.1, 5.8).
    """
    clock = Test_Clock(start=now)
    # Start Introduced so the 0-floor (not the 30-floor) applies: Property 7 is
    # the universal 0-100 bound; the 30-floor-once-Practiced is Property 11.
    mastery = _mastery(start_score, _expected_band(start_score))

    for delta in deltas:
        mastery = apply_delta(mastery, delta, clock=clock, source="quiz")
        assert isinstance(mastery.score, int)
        assert MASTERY_MIN <= mastery.score <= MASTERY_MAX


# =============================================================================
# Property 8: Mastery state matches its threshold band and gate
# (Req 5.2, 5.3, 5.5, 5.6)
# =============================================================================


@settings(max_examples=200)
@given(
    score=_scores,
    review_passed=st.booleans(),
    lab_covers=st.booleans(),
    lab_passed=st.booleans(),
)
def test_property_8_state_matches_threshold_band_and_gate(
    score: int,
    review_passed: bool,
    lab_covers: bool,
    lab_passed: bool,
) -> None:
    """Feature: learning-core, Property 8: Mastery state matches its threshold band and gate.

    For any mastery value and gate conditions, the derived state matches the
    Config threshold band, and the Mastered state holds only when mastery is at
    least the Mastered threshold, a review has passed, and — where a lab covers
    the concept — the lab check has passed (Req 5.2, 5.3, 5.5, 5.6).
    """
    t = MASTERY_THRESHOLDS
    lab = Fake_LabCheck(
        covered={_CONCEPT} if lab_covers else None,
        passed={_CONCEPT: {_USER}} if (lab_covers and lab_passed) else None,
    )

    state = derive_state(
        score,
        encountered=True,
        review_passed=review_passed,
        lab_check=lab,
        concept_id=_CONCEPT,
        user_id=_USER,
    )

    # The lab-check condition: satisfied when no lab covers, else when it passed.
    lab_ok = (not lab_covers) or lab_passed
    mastered_gate_met = score >= t.mastered and review_passed and lab_ok

    if mastered_gate_met:
        assert state is MasteryState.MASTERED
    elif score >= t.proficient:
        # Proficient band, or numeric-Mastered band with the gate unmet.
        assert state is MasteryState.PROFICIENT
    elif score >= t.practiced:
        assert state is MasteryState.PRACTICED
    else:
        assert state is MasteryState.INTRODUCED

    # Mastered holds *only* when the full gate is met — never on score alone.
    if state is MasteryState.MASTERED:
        assert score >= t.mastered and review_passed and lab_ok


# =============================================================================
# Property 9: Every mastery change appends exactly one event (Req 5.7)
# =============================================================================


@settings(max_examples=200)
@given(
    start_score=_scores,
    deltas=st.lists(_deltas, min_size=1, max_size=15),
    now=_instants,
    step=st.integers(min_value=1, max_value=72).map(lambda h: timedelta(hours=h)),
)
def test_property_9_every_change_appends_exactly_one_event(
    start_score: int,
    deltas: list[int],
    now: datetime,
    step: timedelta,
) -> None:
    """Feature: learning-core, Property 9: Every mastery change appends exactly one event.

    For any applied mastery change, exactly one mastery_event is appended
    recording the delta, the source, and the occurrence instant read from the
    injected Clock (Req 5.7).
    """
    clock = Test_Clock(start=now)
    mastery = _mastery(start_score, _expected_band(start_score))

    for expected_count, delta in enumerate(deltas, start=1):
        clock.advance(step)
        before_score = mastery.score
        mastery = apply_delta(mastery, delta, clock=clock, source="quiz")

        # Exactly one new event per change, and the history only grows.
        assert len(mastery.events) == expected_count
        event = mastery.events[-1]
        # The event records the *effective* delta actually applied after cap/floor.
        assert event.delta == mastery.score - before_score
        assert event.source == "quiz"
        assert event.occurred_at == clock.now()


# =============================================================================
# Property 11: Decay below a threshold once Practiced never falls below
# Practiced or 30 (Req 5.9, 5.10, 5.11, 8.2, 8.5)
# =============================================================================


@settings(max_examples=200)
@given(
    start_score=st.integers(min_value=MASTERY_THRESHOLDS.proficient, max_value=MASTERY_MAX),
    days_overdue=_overdue_days,
    now=_instants,
)
def test_property_11_decay_once_practiced_never_below_practiced_or_30(
    start_score: int,
    days_overdue: int,
    now: datetime,
) -> None:
    """Feature: learning-core, Property 11: Decay below a threshold once Practiced
    never falls below Practiced or 30.

    For any concept at Mastered or Proficient, decay that lowers mastery below a
    band threshold transitions it exactly one band down (Mastered->Proficient
    below the Mastered threshold, Proficient->Practiced below the Proficient
    threshold), never below Practiced, and never below a mastery of 30 once the
    concept has reached Practiced (Req 5.9, 5.10, 5.11, 8.2, 8.5).
    """
    t = MASTERY_THRESHOLDS
    clock = Test_Clock(start=now)
    start_state = MasteryState.PROFICIENT if start_score < t.mastered else MasteryState.MASTERED
    mastery = _mastery(start_score, start_state)

    after = apply_decay(mastery, days_overdue=days_overdue, clock=clock)

    # The raw reduction is -1 per overdue day, floored at 30 once Practiced.
    raw = start_score + DECAY_RATE_PER_DAY * days_overdue
    expected_score = max(raw, DECAY_FLOOR_ONCE_PRACTICED)
    assert after.score == expected_score

    # Never below the 30 floor and never relocked below the Practiced band once
    # Practiced (so never Introduced/Unseen) (Req 5.11).
    assert after.score >= DECAY_FLOOR_ONCE_PRACTICED
    assert after.state in (MasteryState.PRACTICED, MasteryState.PROFICIENT, MasteryState.MASTERED)

    # The band transitions exactly one step down as thresholds are crossed; a
    # decayed concept never re-satisfies the Mastered gate on its own, so the
    # best it can be after decay is Proficient.
    if after.score < t.proficient:
        assert after.state is MasteryState.PRACTICED
    else:  # still in the Proficient band (60-84) or capped region below Mastered
        assert after.state is MasteryState.PROFICIENT


# =============================================================================
# Edge-case rules tests — exact band and overdue-day boundaries
# =============================================================================


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (MASTERY_THRESHOLDS.practiced - 1, MasteryState.INTRODUCED),
        (MASTERY_THRESHOLDS.practiced, MasteryState.PRACTICED),
        (MASTERY_THRESHOLDS.proficient - 1, MasteryState.PRACTICED),
        (MASTERY_THRESHOLDS.proficient, MasteryState.PROFICIENT),
        (MASTERY_THRESHOLDS.mastered - 1, MasteryState.PROFICIENT),
        (MASTERY_THRESHOLDS.mastered, MasteryState.PROFICIENT),  # gate unmet
    ],
)
def test_band_boundaries_are_inclusive_lower_bounds(
    score: int,
    expected: MasteryState,
) -> None:
    # The written bands are < 30, 30-59, 60-84, >= 85 with inclusive lower
    # bounds (RULES-REVIEW D7 §3.2). The numeric Mastered threshold with the gate
    # unmet stays Proficient (Req 5.6).
    assert derive_state(score, encountered=True) is expected


def test_zero_overdue_days_applies_no_decay() -> None:
    # A review that is not overdue (0 complete days) applies no reduction, and a
    # mastery change of 0 still appends exactly one event recording delta 0
    # (Req 5.7, 8.1).
    clock = Test_Clock(start=_START)
    before = _mastery(MASTERY_THRESHOLDS.proficient + 5, MasteryState.PROFICIENT)
    after = apply_decay(before, days_overdue=0, clock=clock)
    assert after.score == before.score
    assert after.events[-1].delta == 0
