"""End-to-end domain test of the worked mastery example (task 7.1).

Encodes ``docs/RULES-REVIEW.md`` (D7) section 9: one learner, Lea, walks the
concept ``linux-permissions`` through the mastery model on a single timeline
driven by ``Test_Clock`` -- including one hint reveal and one overdue review.

It composes the real domain services (mastery, antifarming, hints, review,
decay) with ``Fake_LabCheck``; no database and no wall clock (Req 11.4).

Every threshold, floor, rate, interval, window and the hint multiplier is read
from ``edu_it_domain.config``. The per-event point magnitudes (quiz, lab,
passed review) are the *illustrative* values of D7 section 9 -- they are not
config business numbers, so they are named here once as test-local constants.
The hint-tempered gain is derived from ``HINT_MULTIPLIER``, not hardcoded.

**Validates: Requirements 11.2, 11.4**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.config import (
    ANTI_FARMING_WINDOW,
    DECAY_FLOOR_ONCE_PRACTICED,
    DECAY_RATE_PER_DAY,
    HINT_MULTIPLIER,
    INITIAL_REVIEW_INTERVAL,
    MASTERY_MAX,
    MASTERY_THRESHOLDS,
    PASSED_REVIEW_INTERVAL,
)
from edu_it_domain.learning import decay, review
from edu_it_domain.learning.antifarming import evaluate_reanswer
from edu_it_domain.learning.hints import HINT_SOURCE, record_hint_reveal, tempered_gain
from edu_it_domain.learning.mastery import apply_delta
from edu_it_domain.learning.models import Mastery, MasteryState
from edu_it_infrastructure.fakes import Fake_LabCheck, Test_Clock

# --- Setup (D7 section 9) -----------------------------------------------------

_D0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_CONCEPT = "concept-linux-permissions"

# Illustrative per-event magnitudes from D7 section 9 (not normative config).
_QUIZ_GAIN = 18
_LAB_GAIN = 22
_REVIEW_GAIN = 6


def _day(n: int) -> datetime:
    return _D0 + timedelta(days=n)


def test_worked_example_mastery_trajectory_hint_and_overdue_decay() -> None:
    clock = Test_Clock(start=_D0)
    lab = Fake_LabCheck(covered=[_CONCEPT])  # C is covered by a lab (D7 setup)

    # --- D0: first encounter + Q1 correct: 0 -> 18, Introduced ---------------
    m = Mastery(user_id=_USER, concept_id=_CONCEPT, score=0, state=MasteryState.INTRODUCED)
    m = apply_delta(m, _QUIZ_GAIN, clock=clock, source="quiz")
    assert (m.score, m.state) == (18, MasteryState.INTRODUCED)
    assert m.score < MASTERY_THRESHOLDS.practiced

    # --- D1: Q2 correct: 18 -> 36, crosses 30 -> Practiced, enters review ----
    clock.set(_day(1))
    m = apply_delta(m, _QUIZ_GAIN, clock=clock, source="quiz")
    assert (m.score, m.state) == (36, MasteryState.PRACTICED)
    q2_first_answered_at = clock.now()

    schedule = review.schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    assert schedule.interval == INITIAL_REVIEW_INTERVAL
    assert schedule.due_at == _day(1) + INITIAL_REVIEW_INTERVAL == _day(8)

    # --- D1 (+12h): Q2 re-answered inside 24h: no additional mastery ---------
    clock.set(q2_first_answered_at + timedelta(hours=12))
    reanswer = evaluate_reanswer("q2", q2_first_answered_at, clock=clock)
    assert reanswer.counted_for_mastery is False
    events_before = len(m.events)
    if reanswer.counted_for_mastery:  # not taken; mirrors how a caller gates gain
        m = apply_delta(m, _QUIZ_GAIN, clock=clock, source="quiz")
    assert (m.score, len(m.events)) == (36, events_before)

    # The window boundary is exact: at first_answered_at + window it counts again.
    clock.set(q2_first_answered_at + ANTI_FARMING_WINDOW)
    assert evaluate_reanswer("q2", q2_first_answered_at, clock=clock).counted_for_mastery

    # --- D3: Q3 with a hint revealed first: tempered gain, 36 -> 45 ----------
    clock.set(_day(3))
    hinted_gain = tempered_gain(_QUIZ_GAIN, hint_revealed_before_submission=True)
    assert hinted_gain == int(_QUIZ_GAIN * HINT_MULTIPLIER) == 9
    assert hinted_gain < _QUIZ_GAIN
    events_before = len(m.events)
    reveal, m = record_hint_reveal(m, clock=clock, base_gain=_QUIZ_GAIN)
    assert reveal.revealed_at == _day(3)
    assert m.score == 45
    assert m.state is MasteryState.PRACTICED
    assert len(m.events) == events_before + 1
    assert m.events[-1].source == HINT_SOURCE
    assert m.events[-1].delta == hinted_gain

    # --- D4: Q4 -> 63 (Proficient), Q5 -> 81 (still Proficient) --------------
    clock.set(_day(4))
    m = apply_delta(m, _QUIZ_GAIN, clock=clock, source="quiz")
    assert (m.score, m.state) == (63, MasteryState.PROFICIENT)
    m = apply_delta(m, _QUIZ_GAIN, clock=clock, source="quiz")
    assert (m.score, m.state) == (81, MasteryState.PROFICIENT)

    # --- D6: lab check passes: raw 103 capped at 100; gate needs a review ----
    clock.set(_day(6))
    lab.set_passed(_CONCEPT, _USER, passed=True)
    m = apply_delta(m, _LAB_GAIN, clock=clock, source="lab", lab_check=lab)
    assert m.score == MASTERY_MAX
    assert m.events[-1].delta == MASTERY_MAX - 81  # effective delta after the cap
    assert m.score >= MASTERY_THRESHOLDS.mastered
    assert m.state is MasteryState.PROFICIENT  # no passed review yet

    # --- D8: review becomes due; not yet attempted, nothing overdue ----------
    clock.set(_day(8))
    assert review.is_due(schedule, clock=clock)
    assert decay.overdue_days(schedule.due_at, clock=clock) == 0
    assert decay.apply_decay(m, schedule, clock=clock) is m  # no decay owed

    # --- D11: 3 days overdue -> decay of exactly 3 * rate, deterministic -----
    clock.set(_day(11))
    assert decay.overdue_days(schedule.due_at, clock=clock) == 3
    decayed = decay.apply_decay(m, schedule, clock=clock, lab_check=lab)
    assert decayed.score == MASTERY_MAX + 3 * DECAY_RATE_PER_DAY == 97
    assert decayed.events[-1].delta == 3 * DECAY_RATE_PER_DAY
    assert decayed.score >= DECAY_FLOOR_ONCE_PRACTICED
    assert decayed.state is MasteryState.PROFICIENT  # re-queued, never relocked
    assert decayed.state not in (MasteryState.UNSEEN, MasteryState.INTRODUCED)

    # Idempotent per instant: re-running decay at the same instant changes nothing.
    again = decay.apply_decay(decayed, schedule, clock=clock, lab_check=lab)
    assert again is decayed
    assert len(again.events) == len(decayed.events)

    # --- D11: review passed: +6 capped at 100; Mastered gate now met ---------
    schedule = review.record_review(schedule, passed=True, clock=clock)
    assert schedule.interval == PASSED_REVIEW_INTERVAL
    assert schedule.due_at == _day(11) + PASSED_REVIEW_INTERVAL
    assert [a.passed for a in schedule.history] == [True]

    final = apply_delta(
        decayed, _REVIEW_GAIN, clock=clock, source="review", review_passed=True, lab_check=lab
    )
    assert final.score == MASTERY_MAX  # 97 + 6 = 103 capped
    assert final.events[-1].delta == MASTERY_MAX - 97
    assert final.score >= MASTERY_THRESHOLDS.mastered
    assert final.state is MasteryState.MASTERED

    # Every change appended exactly one event, in timeline order.
    assert [e.source for e in final.events] == [
        "quiz", "quiz", HINT_SOURCE, "quiz", "quiz", "lab", "decay", "review",
    ]  # fmt: skip
    assert [e.occurred_at for e in final.events] == sorted(e.occurred_at for e in final.events)


def test_mastered_gate_requires_lab_check_where_a_lab_covers_the_concept() -> None:
    # Same end state as D11 but the covering lab check was never passed:
    # numeric >= mastered threshold and a passed review are not enough (Req 5.6).
    clock = Test_Clock(start=_day(11))
    lab = Fake_LabCheck(covered=[_CONCEPT])  # covered, learner has NOT passed
    m = Mastery(user_id=_USER, concept_id=_CONCEPT, score=97, state=MasteryState.PROFICIENT)
    m = apply_delta(
        m, _REVIEW_GAIN, clock=clock, source="review", review_passed=True, lab_check=lab
    )
    assert m.score == MASTERY_MAX
    assert m.state is MasteryState.PROFICIENT
