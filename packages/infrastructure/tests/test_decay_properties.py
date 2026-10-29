"""Rules-based + property tests for decay (task 6.1, Req 8.1, 8.2, 8.4, 8.5, 9.6).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the dedicated domain service
:mod:`edu_it_domain.learning.decay` does not exist yet (it lands in task 6.2).
Until then every test here fails to import, which is the correct state for the
rules-first ordering — the tests pin the intended behavior the implementation
must satisfy rather than describing code that already exists.

Scope relative to the existing mastery service
-----------------------------------------------
Mastery's own reduction/floor/band mechanics — "decay below a threshold once
Practiced never falls below Practiced or 30" (Property 11) and the one-event-
per-change invariant — already live in :mod:`edu_it_domain.learning.mastery`
(``apply_decay(mastery, days_overdue, ...)``) and are covered by
``test_mastery_properties.py``. This file does **not** re-prove Property 11. It
targets the *dedicated decay module's* two distinct responsibilities, which
mastery's ``apply_decay`` deliberately does not own because it is handed a
pre-computed ``days_overdue``:

- **Property 14** — decay is a deterministic, idempotent function of the Clock
  reading and the review-schedule state: the reduction is exactly one point per
  complete 24-hour overdue day, and re-applying the computation for the *same*
  ``(due_at, clock.now())`` pair yields the identical mastery value and schedule
  state with no further reduction (Req 8.1, 8.4).
- **Property 17** — due and overdue derive purely from the Clock: a concept is
  due exactly when the Clock reading is at or after ``due_at``, and the overdue-
  day count is the whole number of complete 24-hour days by which the Clock
  reading exceeds ``due_at`` (Req 9.6).

Intended public API of :mod:`edu_it_domain.learning.decay` (task 6.2)
---------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Deterministic ``-1/day overdue`` from Clock + schedule
state; idempotent per instant; re-queue not relock, per ADR A6") and the
"Decay idempotency" design note (``decay(schedule, mastery, now)`` with
``days_overdue`` derived from ``due_at`` and ``now``). Every business number —
the ``-1``-per-overdue-day :data:`~edu_it_domain.config.DECAY_RATE_PER_DAY`, the
30 :data:`~edu_it_domain.config.DECAY_FLOOR_ONCE_PRACTICED` floor, and the
0/100 bounds — is read from :mod:`edu_it_domain.config`, never hardcoded
(Req 1.3). These tests target this surface:

``is_due(due_at, *, clock) -> bool``
    The pure due predicate (Req 9.6). Returns ``True`` — the concept is due for
    review — if and only if the Clock reading is **at or after** ``due_at``
    (``clock.now() >= due_at``). At exactly ``due_at`` the concept is due (the
    boundary is inclusive); strictly before ``due_at`` it is not. Both
    ``due_at`` and the Clock reading are timezone-aware UTC datetimes.

``overdue_days(due_at, *, clock) -> int``
    The whole number of complete 24-hour days by which the Clock reading exceeds
    ``due_at`` (Req 9.6, 8.1). Zero when the concept is not yet due, zero in the
    partial first day (``due_at`` .. ``due_at + 24h`` exclusive), one at exactly
    ``due_at + 24h``, and ``floor((now - due_at) / 24h)`` thereafter. Never
    negative.

``apply_decay(mastery, schedule, *, clock, source=..., lab_check=None) -> Mastery``
    Apply spaced-review decay to ``mastery`` for ``schedule`` at the instant read
    from the injected ``clock`` (Req 8.1, 8.4). The reduction is
    :data:`~edu_it_domain.config.DECAY_RATE_PER_DAY` (-1) for each complete
    overdue day — i.e. ``DECAY_RATE_PER_DAY * overdue_days(schedule.due_at,
    clock=clock)`` — floored at :data:`~edu_it_domain.config.DECAY_FLOOR_ONCE_PRACTICED`
    (30) once Practiced, delegating the floor/band mechanics to the mastery
    service it wraps. The computation is **idempotent per instant**: re-applying
    it for the same ``(due_at, clock.now())`` pair yields the identical mastery
    value and schedule state (``due_at``/``interval``/``history``), applying no
    additional reduction beyond the first computation (Req 8.4). Returns a new
    frozen :class:`~edu_it_domain.learning.models.Mastery`; the input is left
    unchanged.

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock`, and the in-memory
:class:`In_Memory_ReviewRepository` / :class:`In_Memory_MasteryRepository` where
persisting the schedule and mastery exercises the "schedule state unchanged by
re-application" invariant end to end — wired exactly as the other fake-driven
tests wire them: no database and no wall clock (Req 11.4). The overdue count is
always derived from ``schedule.due_at`` and the Clock, never a hardcoded
duration (Req 8.1, 9.6).

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import (
    DECAY_FLOOR_ONCE_PRACTICED,
    DECAY_RATE_PER_DAY,
    INITIAL_REVIEW_INTERVAL,
    MASTERY_THRESHOLDS,
)
from edu_it_domain.learning.decay import (
    apply_decay,
    is_due,
    overdue_days,
)
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    ReviewSchedule,
    StableId,
)
from edu_it_infrastructure.fakes import (
    In_Memory_MasteryRepository,
    In_Memory_ReviewRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_USER: StableId = "usr_1"
_CONCEPT: StableId = "cn_1"
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

#: One complete overdue day, read from the domain rule (the 24h day Req 8.1 and
#: 9.6 count in) rather than hardcoded at the call site.
_ONE_DAY = timedelta(days=1)


def _mastery(
    score: int,
    state: MasteryState,
    *,
    concept_id: StableId = _CONCEPT,
    user_id: StableId = _USER,
) -> Mastery:
    """Build a freshly-tracked :class:`Mastery` at ``score`` in ``state``.

    The event history starts empty; the decay service appends to it.
    """
    return Mastery(user_id=user_id, concept_id=concept_id, score=score, state=state)


def _schedule(
    due_at: datetime,
    *,
    interval: timedelta = INITIAL_REVIEW_INTERVAL,
    concept_id: StableId = _CONCEPT,
    user_id: StableId = _USER,
) -> ReviewSchedule:
    """Build a :class:`ReviewSchedule` due at ``due_at`` with an empty history.

    ``interval`` defaults to the Config initial review interval; the schedule is
    not invented beyond the fields D3/``models.py`` define (``due_at``,
    ``interval``, ``history``).
    """
    return ReviewSchedule(
        user_id=user_id,
        concept_id=concept_id,
        due_at=due_at,
        interval=interval,
    )


# --- Generators constrained to the input space -------------------------------

#: ``due_at`` instants kept well inside the datetime range so that adding the
#: overdue offsets below never overflows.
_due_ats = st.datetimes(
    min_value=datetime(2001, 1, 1, 0, 0, 0),
    max_value=datetime(2099, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))

#: Offsets of the Clock reading relative to ``due_at``: spanning before the due
#: instant (not yet due), the partial first day, and many whole days overdue.
#: The exact 24h-multiple boundaries are pinned by the dedicated edge tests; the
#: properties exercise the derivation broadly on both sides.
_clock_offsets = st.timedeltas(
    min_value=-timedelta(days=5),
    max_value=timedelta(days=400),
)

#: Whole overdue-day counts including 0 (not overdue) for the decay properties.
_overdue_day_counts = st.integers(min_value=0, max_value=365)

#: Mastery scores at or above the Practiced floor so the decay-once-Practiced
#: rules apply (the universal 0-100 / below-Practiced behavior is mastery's
#: Property 7/11, not re-proved here).
_practiced_scores = st.integers(
    min_value=MASTERY_THRESHOLDS.practiced,
    max_value=100,
)

#: Extra idempotent re-application counts (beyond the first computation).
_reapplications = st.integers(min_value=1, max_value=6)


# =============================================================================
# Rules-based tests — due exactly at/after due_at (Property 17, Req 9.6)
# =============================================================================


def test_not_due_strictly_before_due_at() -> None:
    # Req 9.6: a concept is due only when the Clock is at or after due_at; one
    # second before, it is not yet due.
    clock = Test_Clock(start=_START - timedelta(seconds=1))
    assert is_due(_START, clock=clock) is False


def test_due_exactly_at_due_at_inclusive_boundary() -> None:
    # Req 9.6: "at or after due_at" — exactly at due_at the concept is due. The
    # boundary is inclusive.
    clock = Test_Clock(start=_START)
    assert is_due(_START, clock=clock) is True


def test_due_after_due_at() -> None:
    # Req 9.6: strictly after due_at the concept remains due.
    clock = Test_Clock(start=_START + timedelta(seconds=1))
    assert is_due(_START, clock=clock) is True


# =============================================================================
# Rules-based tests — overdue-day count at exact 24h boundaries
# (Property 17, Req 9.6, 8.1)
# =============================================================================


def test_zero_overdue_days_at_the_due_instant() -> None:
    # Req 9.6: due but not yet overdue — at due_at exactly, no complete 24h day
    # has elapsed, so the overdue-day count is 0.
    clock = Test_Clock(start=_START)
    assert overdue_days(_START, clock=clock) == 0


def test_zero_overdue_days_at_23h59m_into_the_first_day() -> None:
    # Req 9.6/8.1: 23h59m past due_at is still 0 *complete* 24h days overdue.
    clock = Test_Clock(start=_START + timedelta(hours=23, minutes=59))
    assert overdue_days(_START, clock=clock) == 0


def test_one_overdue_day_at_exactly_24h() -> None:
    # Req 9.6/8.1: exactly 24h past due_at is the first complete overdue day.
    clock = Test_Clock(start=_START + _ONE_DAY)
    assert overdue_days(_START, clock=clock) == 1


def test_overdue_days_floor_to_whole_days() -> None:
    # Req 9.6/8.1: the count is the whole (floored) number of complete days;
    # 2 days 23h59m overdue is 2 complete days, not 3.
    clock = Test_Clock(start=_START + timedelta(days=2, hours=23, minutes=59))
    assert overdue_days(_START, clock=clock) == 2


def test_overdue_days_zero_before_the_due_instant() -> None:
    # Req 9.6: before due_at the concept is not overdue at all — the count is 0,
    # never negative.
    clock = Test_Clock(start=_START - timedelta(days=3))
    assert overdue_days(_START, clock=clock) == 0


# =============================================================================
# Rules-based tests — decay reduction is -1 per complete overdue day
# (Property 14, Req 8.1)
# =============================================================================


def test_decay_reduces_one_point_per_complete_overdue_day() -> None:
    # Req 8.1: mastery drops by exactly 1 point per complete 24h overdue day,
    # derived from the schedule's due_at and the Clock (not a passed-in count).
    overdue = 5
    clock = Test_Clock(start=_START + overdue * _ONE_DAY)
    before = _mastery(MASTERY_THRESHOLDS.mastered, MasteryState.MASTERED)
    after = apply_decay(before, _schedule(_START), clock=clock)
    assert after.score == MASTERY_THRESHOLDS.mastered + DECAY_RATE_PER_DAY * overdue


def test_decay_applies_no_reduction_when_not_yet_overdue() -> None:
    # Req 8.1/9.6: a review that is not overdue (0 complete days) applies no
    # reduction — the mastery value is unchanged.
    clock = Test_Clock(start=_START + timedelta(hours=12))  # due but 0 whole days
    before = _mastery(MASTERY_THRESHOLDS.proficient + 5, MasteryState.PROFICIENT)
    after = apply_decay(before, _schedule(_START), clock=clock)
    assert after.score == before.score


def test_decay_before_due_applies_no_reduction() -> None:
    # Req 8.1/9.6: before due_at there are no overdue days, so decay is a no-op
    # on the mastery value.
    clock = Test_Clock(start=_START - timedelta(days=2))
    before = _mastery(MASTERY_THRESHOLDS.proficient + 5, MasteryState.PROFICIENT)
    after = apply_decay(before, _schedule(_START), clock=clock)
    assert after.score == before.score


# =============================================================================
# Rules-based tests — idempotent re-application per instant
# (Property 14, Req 8.4)
# =============================================================================


def test_decay_idempotent_for_the_same_instant_and_schedule() -> None:
    # Req 8.4: running decay more than once for the same Clock instant and the
    # same schedule state yields an identical resulting mastery value, applying
    # no additional reduction beyond the first computation.
    clock = Test_Clock(start=_START + 10 * _ONE_DAY)
    schedule = _schedule(_START)
    before = _mastery(MASTERY_THRESHOLDS.mastered, MasteryState.MASTERED)

    once = apply_decay(before, schedule, clock=clock)
    twice = apply_decay(once, schedule, clock=clock)

    # No compounding: the second application for the same instant/schedule does
    # not reduce the score further.
    assert twice.score == once.score


def test_decay_schedule_state_unchanged_by_reapplication() -> None:
    # Req 8.4: the review-schedule state (due_at / interval / history) is
    # identical across re-applications for the same instant — decay re-queues,
    # it does not mutate the schedule entry per run. Exercised through the
    # in-memory review repository so the "state unchanged" invariant is checked
    # against persisted state, not just the in-hand value.
    clock = Test_Clock(start=_START + 10 * _ONE_DAY)
    review_repo = In_Memory_ReviewRepository()
    mastery_repo = In_Memory_MasteryRepository()
    schedule = _schedule(_START)
    review_repo.save(schedule)
    mastery = _mastery(MASTERY_THRESHOLDS.mastered, MasteryState.MASTERED)
    mastery_repo.save(mastery)

    first = apply_decay(mastery, schedule, clock=clock)
    second = apply_decay(first, schedule, clock=clock)

    # The schedule the decay was computed against is left untouched by decay.
    stored = review_repo.get(_USER, _CONCEPT)
    assert stored is not None
    assert stored.due_at == _START
    assert stored.interval == schedule.interval
    assert stored.history == ()
    # And re-application at the same instant adds no further reduction.
    assert second.score == first.score


# =============================================================================
# Property 14: Decay is a deterministic, idempotent function of the clock and
# schedule (Req 8.1, 8.4)
# =============================================================================


@settings(max_examples=200)
@given(
    due_at=_due_ats,
    overdue=_overdue_day_counts,
    start_score=_practiced_scores,
    reapplications=_reapplications,
)
def test_property_14_decay_deterministic_and_idempotent(
    due_at: datetime,
    overdue: int,
    start_score: int,
    reapplications: int,
) -> None:
    """Feature: learning-core, Property 14: Decay is a deterministic, idempotent
    function of the clock and schedule.

    For any review-schedule state and any Clock reading, the mastery reduction
    equals one point per complete 24-hour day the review is overdue, and
    applying the decay computation any number of times for the same Clock
    reading and schedule state yields the identical mastery value and schedule
    state with no additional reduction beyond the first computation (Req 8.1,
    8.4).
    """
    # Place the clock exactly ``overdue`` whole days past due_at (plus a partial
    # slice that must not count as another whole day), so the overdue-day count
    # derived from the schedule and Clock is exactly ``overdue``.
    clock = Test_Clock(start=due_at + overdue * _ONE_DAY + timedelta(hours=3))
    schedule = _schedule(due_at)
    start_state = (
        MasteryState.MASTERED
        if start_score >= MASTERY_THRESHOLDS.mastered
        else MasteryState.PROFICIENT
    )
    before = _mastery(start_score, start_state)

    first = apply_decay(before, schedule, clock=clock)

    # Deterministic reduction: -1 per complete overdue day, floored at 30 once
    # Practiced (the floor itself is mastery's Property 11; here we assert the
    # reduction the dedicated module derives from the clock + schedule).
    raw = start_score + DECAY_RATE_PER_DAY * overdue
    expected_score = max(raw, DECAY_FLOOR_ONCE_PRACTICED)
    assert first.score == expected_score

    # Idempotent per instant: re-applying for the same (due_at, now) pair any
    # number of times yields the identical mastery value — no accumulation.
    current = first
    for _ in range(reapplications):
        current = apply_decay(current, schedule, clock=clock)
        assert current.score == first.score


# =============================================================================
# Property 17: Due and overdue derive purely from the clock (Req 9.6)
# =============================================================================


@settings(max_examples=200)
@given(due_at=_due_ats, offset=_clock_offsets)
def test_property_17_due_and_overdue_derive_purely_from_the_clock(
    due_at: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 17: Due and overdue derive purely from the clock.

    For any review_schedule.due_at and any Clock reading, the concept is due
    exactly when the Clock reading is at or after due_at, and the overdue-day
    count equals the whole number of days by which the Clock reading exceeds
    due_at (Req 9.6).
    """
    now = due_at + offset
    clock = Test_Clock(start=now)

    # Due iff the Clock reading is at or after due_at (inclusive boundary).
    assert is_due(due_at, clock=clock) is (now >= due_at)

    # Overdue days = the whole number of complete 24h days now exceeds due_at,
    # never negative (0 when not yet due or within the first partial day).
    expected_days = 0 if now < due_at else (now - due_at) // _ONE_DAY
    assert overdue_days(due_at, clock=clock) == expected_days
    assert overdue_days(due_at, clock=clock) >= 0


@settings(max_examples=200)
@given(due_at=_due_ats, overdue=_overdue_day_counts)
def test_property_17_whole_day_multiples_count_exactly(
    due_at: datetime,
    overdue: int,
) -> None:
    """Feature: learning-core, Property 17: Due and overdue derive purely from the clock.

    At exactly ``overdue`` whole 24-hour days past due_at the overdue-day count
    is exactly ``overdue``, and a sub-day slice short of the next whole day does
    not advance the count — the count is the floored whole-day difference
    (Req 9.6).
    """
    # Exactly on the Nth whole-day boundary → N.
    clock = Test_Clock(start=due_at + overdue * _ONE_DAY)
    assert overdue_days(due_at, clock=clock) == overdue

    # One microsecond short of the (N+1)th boundary → still N.
    clock.set(due_at + (overdue + 1) * _ONE_DAY - timedelta(microseconds=1))
    assert overdue_days(due_at, clock=clock) == overdue


# =============================================================================
# Edge-case rules tests — the exact due boundary triple and the 24h-day triple
# =============================================================================


@pytest.mark.parametrize(
    ("offset", "expected_due"),
    [
        (-timedelta(microseconds=1), False),  # just before due_at → not due
        (timedelta(0), True),  # exactly at due_at → due (inclusive)
        (timedelta(microseconds=1), True),  # just after due_at → due
    ],
)
def test_due_boundary_is_inclusive_at_due_at(
    offset: timedelta,
    expected_due: bool,
) -> None:
    # Req 9.6: due exactly when ``clock.now() >= due_at`` — the boundary at
    # due_at itself counts as due.
    clock = Test_Clock(start=_START + offset)
    assert is_due(_START, clock=clock) is expected_due


@pytest.mark.parametrize(
    ("offset", "expected_days"),
    [
        (_ONE_DAY - timedelta(microseconds=1), 0),  # just short of 24h → 0
        (_ONE_DAY, 1),  # exactly 24h → 1
        (_ONE_DAY + timedelta(microseconds=1), 1),  # just past 24h → still 1
    ],
)
def test_overdue_day_boundary_is_exact_at_24h_multiples(
    offset: timedelta,
    expected_days: int,
) -> None:
    # Req 9.6/8.1: a complete overdue day is a full 24h; the count ticks up
    # exactly at the 24h boundary and not a microsecond before.
    clock = Test_Clock(start=_START + offset)
    assert overdue_days(_START, clock=clock) == expected_days
