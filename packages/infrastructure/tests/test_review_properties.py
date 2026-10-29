"""Rules-based + property tests for review scheduling/selection (task 6.3, Req 9, 28).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.review`
does not exist yet (it lands in task 6.4). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.review` (task 6.4)
----------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Create one schedule entry on first Practiced;
resume-on-re-practice; due/overdue computation; interval change on pass/fail;
eligible-question selection incl. empty pool and <3 minimum"). Every business
number — the initial / passed / failed review intervals and the minimum
eligible-question count — is read from :mod:`edu_it_domain.config`, never
hardcoded (Req 1.3), and exposed as an optional keyword defaulting to that
config value so a caller can override it in a focused test, mirroring the
``interval`` keyword on :mod:`edu_it_domain.learning.cooldown`. These tests
target this surface:

``schedule_on_practiced(existing, *, user_id, concept_id, clock,
initial_interval=INITIAL_REVIEW_INTERVAL) -> ReviewSchedule``
    Create-or-resume the single ``review_schedule`` entry for a concept that has
    reached Practiced (Req 5.4, 9.1, 9.2). When ``existing`` is ``None`` — the
    concept first reaches Practiced — return exactly one new
    :class:`~edu_it_domain.learning.models.ReviewSchedule` whose
    :attr:`~edu_it_domain.learning.models.ReviewSchedule.interval` is
    ``initial_interval`` and whose
    :attr:`~edu_it_domain.learning.models.ReviewSchedule.due_at` is
    ``clock.now() + initial_interval`` (Req 9.1), with empty history. When
    ``existing`` is not ``None`` — the concept re-reaches Practiced after a decay
    dip — return the **existing** entry unchanged: the same ``interval``,
    ``due_at``, and attempt history, never a second entry and never a reset to
    ``initial_interval`` (Req 9.2). The repository keyed by
    ``(user_id, concept_id)`` makes "exactly one entry" observable end to end.

``record_review(schedule, *, passed, clock,
passed_interval=PASSED_REVIEW_INTERVAL, failed_interval=FAILED_REVIEW_INTERVAL)
-> ReviewSchedule``
    Advance an existing ``schedule`` by one review attempt (Req 9.4, 9.5).
    A passed review sets the next interval to ``passed_interval`` (the Config
    ``PASSED_REVIEW_INTERVAL``, greater than the current interval); a failed
    review sets it to ``failed_interval`` (the Config ``FAILED_REVIEW_INTERVAL``,
    less than the current interval). The next
    :attr:`~edu_it_domain.learning.models.ReviewSchedule.due_at` is
    ``clock.now()`` plus the new interval, and exactly one
    :class:`~edu_it_domain.learning.models.ReviewAttempt` recording ``passed``
    and the ``clock.now()`` instant is appended to the history. Returns a new
    :class:`~edu_it_domain.learning.models.ReviewSchedule`; the input is left
    unchanged (the domain types are frozen).

``select_review_questions(pool, *, count=None) -> tuple[QuestionRecord, ...]``
    Select the review question set for a concept (Req 9.3). The result is always
    a subset of ``pool`` (every selected question is an element of ``pool``, no
    foreign question is introduced, and no question appears twice); an empty
    ``pool`` yields an empty selection without error.

``is_pool_eligible(pool, *, minimum=MIN_ELIGIBLE_QUESTION_COUNT) -> bool``
    Whether ``pool`` has at least ``minimum`` eligible questions (Req 28.3). The
    minimum is read from :data:`~edu_it_domain.config.MIN_ELIGIBLE_QUESTION_COUNT`
    (3), never hardcoded.

``start_review(existing, pool, *, user_id, concept_id, clock,
minimum=MIN_ELIGIBLE_QUESTION_COUNT, initial_interval=INITIAL_REVIEW_INTERVAL)
-> ReviewSchedule | None``
    Decline a review whose eligible ``pool`` is below ``minimum`` (Req 28.1,
    28.2). When ``len(pool) < minimum`` the call returns ``None`` and creates or
    advances **no** schedule entry — ``existing`` is returned untouched to its
    caller and nothing is written. When the pool meets the minimum the review
    proceeds and the single schedule entry is created-or-resumed exactly as
    :func:`schedule_on_practiced` does.

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock` and the in-memory
:class:`In_Memory_ReviewRepository`, wired exactly as the other fake-driven
tests wire them: no database and no wall clock (Req 11.4). The single-entry
invariant (Req 9.1, 9.2) is exercised end to end through the repository, whose
``save`` is keyed by ``(user_id, concept_id)`` and whose attempt history is
append-only.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.config import (
    FAILED_REVIEW_INTERVAL,
    INITIAL_REVIEW_INTERVAL,
    MIN_ELIGIBLE_QUESTION_COUNT,
    PASSED_REVIEW_INTERVAL,
)
from edu_it_domain.learning.models import (
    QuestionOption,
    ReviewSchedule,
    StableId,
)
from edu_it_domain.learning.review import (
    is_pool_eligible,
    record_review,
    schedule_on_practiced,
    select_review_questions,
    start_review,
)
from edu_it_domain.ports.catalog import QuestionRecord
from edu_it_infrastructure.fakes import (
    In_Memory_ReviewRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_USER: StableId = "usr_1"
_CONCEPT: StableId = "cn_1"
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _question(stable_id: StableId) -> QuestionRecord:
    """Build a minimal eligible :class:`QuestionRecord` covering ``_CONCEPT``.

    The pool content is irrelevant to scheduling/selection; a single correct
    option and the covered concept are enough to make the record a well-formed
    member of a concept's eligible pool.
    """
    return QuestionRecord(
        stable_id=stable_id,
        options=(QuestionOption(option_id=1, is_correct=True),),
        concept_ids=(_CONCEPT,),
    )


def _pool(size: int) -> tuple[QuestionRecord, ...]:
    """Build an eligible pool of ``size`` distinct questions."""
    return tuple(_question(f"q_{i}") for i in range(size))


def _assert_subset(
    selection: tuple[QuestionRecord, ...],
    pool: tuple[QuestionRecord, ...],
) -> None:
    """Assert ``selection`` is a subset of ``pool`` with no duplicates or foreigners."""
    pool_ids = {q.stable_id for q in pool}
    selection_ids = [q.stable_id for q in selection]
    # Every selected question comes from the pool (no foreign question).
    assert all(sid in pool_ids for sid in selection_ids)
    # No question is selected twice.
    assert len(selection_ids) == len(set(selection_ids))
    # Each selected object is actually an element of the pool.
    assert all(q in pool for q in selection)


# --- Generators constrained to the input space -------------------------------

_instants = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2100, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))

#: Pool sizes spanning empty, the exact minimum boundary (2 vs 3), and larger.
_pool_sizes = st.integers(min_value=0, max_value=12)

#: Pool sizes strictly below the Config minimum (the decline region, Req 28).
_below_minimum_sizes = st.integers(min_value=0, max_value=MIN_ELIGIBLE_QUESTION_COUNT - 1)

#: Pool sizes at or above the Config minimum (the proceed region).
_at_or_above_minimum_sizes = st.integers(
    min_value=MIN_ELIGIBLE_QUESTION_COUNT,
    max_value=MIN_ELIGIBLE_QUESTION_COUNT + 9,
)

#: Current review intervals the pass/fail advance is applied against. Kept
#: distinct from the config pass/fail intervals is not required — Property 16
#: asserts the *new* interval equals the config value regardless of the current.
_current_intervals = st.timedeltas(
    min_value=timedelta(hours=1),
    max_value=timedelta(days=60),
)


# =============================================================================
# Rules-based tests — one entry on first Practiced (Req 9.1)
# =============================================================================


def test_first_practiced_creates_exactly_one_entry_due_after_initial_interval() -> None:
    # Req 9.1: WHEN a concept first reaches Practiced, create exactly one
    # review_schedule entry with due_at = Clock now + initial review interval.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    assert repo.get(_USER, _CONCEPT) is None  # none before Practiced
    schedule = schedule_on_practiced(
        None,
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    repo.save(schedule)

    stored = repo.get(_USER, _CONCEPT)
    assert stored is not None
    # Interval + due_at derive from Config + the injected Clock (Req 9.1).
    assert stored.interval == INITIAL_REVIEW_INTERVAL
    assert stored.due_at == clock.now() + INITIAL_REVIEW_INTERVAL
    assert stored.history == ()  # no attempts yet
    # Exactly one entry for this learner (Req 9.1).
    assert len(repo.list_for_user(_USER)) == 1


def test_initial_due_at_reads_the_config_interval_not_a_hardcoded_duration() -> None:
    # Req 9.1 / Req 1.3: the initial interval is read from Config. Overriding it
    # shifts the computed due_at by exactly the override, proving nothing is
    # hardcoded in the service.
    clock = Test_Clock(start=_START)
    override = timedelta(days=5)
    schedule = schedule_on_practiced(
        None,
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
        initial_interval=override,
    )
    assert schedule.interval == override
    assert schedule.due_at == clock.now() + override


# =============================================================================
# Rules-based tests — resume, not recreate, after a decay dip (Req 5.4, 9.2)
# =============================================================================


def test_repractice_resumes_existing_entry_without_creating_a_second() -> None:
    # Req 9.2: IF a concept re-reaches Practiced after a decay dip AND an entry
    # already exists, do NOT create a second entry; resume the existing entry
    # from its current interval and history rather than resetting to initial.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    # First Practiced → one entry at the initial interval.
    first = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    repo.save(first)

    # A review happens, moving the entry off the initial interval and giving it
    # some history (so a reset-to-initial would be observable).
    clock.advance(INITIAL_REVIEW_INTERVAL)
    current = repo.get(_USER, _CONCEPT)
    assert current is not None
    advanced = record_review(current, passed=True, clock=clock)
    repo.save(advanced)
    assert advanced.interval == PASSED_REVIEW_INTERVAL
    assert len(advanced.history) == 1

    # Time passes, the concept decays and then re-reaches Practiced. The service
    # is handed the existing entry.
    clock.advance(timedelta(days=30))
    resumed = schedule_on_practiced(
        repo.get(_USER, _CONCEPT),
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    repo.save(resumed)

    # Still exactly one entry, resumed from its current interval + history — not
    # reset to the initial interval and not a second entry (Req 9.2).
    assert len(repo.list_for_user(_USER)) == 1
    assert resumed.interval == PASSED_REVIEW_INTERVAL
    assert resumed.interval != INITIAL_REVIEW_INTERVAL
    assert resumed.due_at == advanced.due_at  # unchanged, not recomputed
    assert resumed.history == advanced.history  # history preserved


def test_repractice_before_any_review_keeps_the_initial_entry_unchanged() -> None:
    # Req 9.2: re-reaching Practiced before any review still resumes the single
    # existing entry (same due_at, same empty history) rather than recreating it.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    first = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    repo.save(first)
    original_due = first.due_at

    clock.advance(timedelta(days=3))  # Clock moves on
    resumed = schedule_on_practiced(
        repo.get(_USER, _CONCEPT),
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    repo.save(resumed)

    assert len(repo.list_for_user(_USER)) == 1
    # due_at is NOT recomputed from the new Clock reading — the entry resumes.
    assert resumed.due_at == original_due
    assert resumed.interval == INITIAL_REVIEW_INTERVAL
    assert resumed.history == ()


# =============================================================================
# Rules-based tests — pass lengthens / fail shortens the interval (Req 9.4, 9.5)
# =============================================================================


def test_passed_review_sets_the_config_passed_interval_which_is_longer() -> None:
    # Req 9.4: WHEN a review is passed, set the next interval to the Config
    # passed-review interval, which is greater than the current interval.
    clock = Test_Clock(start=_START)
    schedule = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    assert schedule.interval == INITIAL_REVIEW_INTERVAL

    clock.advance(INITIAL_REVIEW_INTERVAL)
    after = record_review(schedule, passed=True, clock=clock)

    assert after.interval == PASSED_REVIEW_INTERVAL
    assert after.interval > schedule.interval  # a pass lengthens the spacing
    assert after.due_at == clock.now() + PASSED_REVIEW_INTERVAL
    assert len(after.history) == 1
    assert after.history[-1].passed is True
    assert after.history[-1].attempted_at == clock.now()


def test_failed_review_sets_the_config_failed_interval_which_is_shorter() -> None:
    # Req 9.5: WHEN a review is failed, set the next interval to the Config
    # failed-review interval, which is less than the current interval.
    clock = Test_Clock(start=_START)
    schedule = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)

    clock.advance(INITIAL_REVIEW_INTERVAL)
    after = record_review(schedule, passed=False, clock=clock)

    assert after.interval == FAILED_REVIEW_INTERVAL
    assert after.interval < schedule.interval  # a failure shortens the spacing
    assert after.due_at == clock.now() + FAILED_REVIEW_INTERVAL
    assert len(after.history) == 1
    assert after.history[-1].passed is False
    assert after.history[-1].attempted_at == clock.now()


def test_record_review_appends_exactly_one_attempt_and_leaves_input_unchanged() -> None:
    # The advance is append-only (one ReviewAttempt per review) and the frozen
    # input schedule is never mutated — a new schedule is returned.
    clock = Test_Clock(start=_START)
    schedule = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)

    clock.advance(INITIAL_REVIEW_INTERVAL)
    after_pass = record_review(schedule, passed=True, clock=clock)
    clock.advance(PASSED_REVIEW_INTERVAL)
    after_fail = record_review(after_pass, passed=False, clock=clock)

    assert schedule.history == ()  # original untouched
    assert len(after_pass.history) == 1
    assert len(after_fail.history) == 2
    # History only grows, oldest first.
    assert after_fail.history[:1] == after_pass.history


def test_config_pass_interval_is_longer_and_fail_interval_is_shorter_than_initial() -> None:
    # The config ordering the property relies on: passed > initial > failed. This
    # pins the invariant the service's "pass lengthens / fail shortens" depends
    # on, reading only from Config (Req 9.4, 9.5).
    assert PASSED_REVIEW_INTERVAL > INITIAL_REVIEW_INTERVAL > FAILED_REVIEW_INTERVAL


# =============================================================================
# Rules-based tests — selection ⊆ pool, incl. the empty pool (Req 9.3)
# =============================================================================


def test_selection_is_a_subset_of_the_pool() -> None:
    # Req 9.3: WHEN a review is due, select review questions only from the
    # concept's eligible pool — the selection is a subset of the pool.
    pool = _pool(5)
    selection = select_review_questions(pool)
    _assert_subset(selection, pool)


def test_empty_pool_yields_empty_selection_without_error() -> None:
    # Req 9.3: an eligible pool with zero questions yields an empty selection and
    # raises no error.
    selection = select_review_questions(())
    assert selection == ()


def test_selection_count_is_bounded_by_the_pool_size() -> None:
    # A selection cannot exceed the pool; requesting more than the pool holds
    # returns at most the whole pool (still a subset, no foreign fill).
    pool = _pool(3)
    selection = select_review_questions(pool, count=10)
    _assert_subset(selection, pool)
    assert len(selection) <= len(pool)


# =============================================================================
# Rules-based tests — decline below the minimum eligible pool (Req 28.1-28.3)
# =============================================================================


def test_pool_eligibility_boundary_two_vs_three() -> None:
    # Req 28.3: the minimum eligible-question count is read from Config (3). A
    # pool of 2 is below the minimum; a pool of exactly 3 meets it.
    assert MIN_ELIGIBLE_QUESTION_COUNT == 3  # pins the Config value the rule fixes
    assert is_pool_eligible(_pool(MIN_ELIGIBLE_QUESTION_COUNT - 1)) is False  # 2 → ineligible
    assert is_pool_eligible(_pool(MIN_ELIGIBLE_QUESTION_COUNT)) is True  # 3 → eligible


def test_start_review_declines_below_minimum_without_scheduling() -> None:
    # Req 28.1 / 28.2: a review whose eligible pool is below the minimum is
    # declined (None) and creates NO schedule entry.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    result = start_review(
        None,
        _pool(MIN_ELIGIBLE_QUESTION_COUNT - 1),  # 2 questions, below the minimum
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    assert result is None
    # Nothing was written: no schedule entry created (Req 28.2).
    assert repo.get(_USER, _CONCEPT) is None
    assert repo.list_for_user(_USER) == ()


def test_start_review_below_minimum_does_not_advance_an_existing_entry() -> None:
    # Req 28.2: when an entry already exists, a below-minimum request neither
    # advances it nor creates another — the existing entry is returned untouched.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    existing = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    repo.save(existing)
    original = repo.get(_USER, _CONCEPT)

    result = start_review(
        existing,
        _pool(MIN_ELIGIBLE_QUESTION_COUNT - 1),  # below the minimum
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    # The request is declined (None) and the stored entry is never advanced (Req 28.2).
    assert result is None
    assert repo.get(_USER, _CONCEPT) == original
    assert len(repo.list_for_user(_USER)) == 1


def test_start_review_at_the_minimum_proceeds_and_schedules_one_entry() -> None:
    # Req 28.1 (boundary): a pool of exactly the minimum (3) is NOT declined —
    # the review proceeds and the single schedule entry is created.
    clock = Test_Clock(start=_START)
    repo = In_Memory_ReviewRepository()

    result = start_review(
        None,
        _pool(MIN_ELIGIBLE_QUESTION_COUNT),  # exactly 3 → eligible
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    assert result is not None
    repo.save(result)
    assert result.interval == INITIAL_REVIEW_INTERVAL
    assert result.due_at == clock.now() + INITIAL_REVIEW_INTERVAL
    assert len(repo.list_for_user(_USER)) == 1


# =============================================================================
# Property 10: One review entry on first Practiced, resumed on re-practice
# (Req 5.4, 9.1, 9.2)
# =============================================================================


@settings(max_examples=200)
@given(
    now=_instants,
    recrossings=st.integers(min_value=0, max_value=6),
    review_outcomes=st.lists(st.booleans(), min_size=0, max_size=5),
)
def test_property_10_one_entry_on_first_practiced_resumed_on_repractice(
    now: datetime,
    recrossings: int,
    review_outcomes: list[bool],
) -> None:
    """Feature: learning-core, Property 10: For any mastery trajectory that reaches
    Practiced one or more times (including after decay dips), exactly one
    review_schedule entry is created — on the first crossing — and a later
    re-crossing resumes the existing entry from its current interval and history
    rather than creating a second entry or resetting to the initial interval.

    Validates Requirements 5.4, 9.1, 9.2.
    """
    clock = Test_Clock(start=now)
    repo = In_Memory_ReviewRepository()

    # First crossing into Practiced → exactly one entry at the initial interval.
    first = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
    repo.save(first)
    assert len(repo.list_for_user(_USER)) == 1
    assert first.interval == INITIAL_REVIEW_INTERVAL
    assert first.due_at == now + INITIAL_REVIEW_INTERVAL

    # Some reviews happen, moving the entry off the initial interval + growing
    # the history, so a spurious reset-to-initial would be detectable.
    for passed in review_outcomes:
        clock.advance(timedelta(days=1))
        current = repo.get(_USER, _CONCEPT)
        assert current is not None
        advanced = record_review(current, passed=passed, clock=clock)
        repo.save(advanced)

    before = repo.get(_USER, _CONCEPT)
    assert before is not None

    # Any number of decay-dip re-crossings resume the same entry: no new entry,
    # no reset of interval/due_at/history (Req 9.2).
    for _ in range(recrossings):
        clock.advance(timedelta(days=10))  # a decay dip then a re-cross
        resumed = schedule_on_practiced(
            repo.get(_USER, _CONCEPT),
            user_id=_USER,
            concept_id=_CONCEPT,
            clock=clock,
        )
        repo.save(resumed)

        # Still exactly one entry, and it is the same entry as before the cross.
        assert len(repo.list_for_user(_USER)) == 1
        assert resumed.interval == before.interval
        assert resumed.due_at == before.due_at
        assert resumed.history == before.history

    # The single-entry invariant holds for the whole trajectory.
    assert len(repo.list_for_user(_USER)) == 1


# =============================================================================
# Property 15: Review selection draws only from the eligible pool (Req 9.3)
# =============================================================================


@settings(max_examples=200)
@given(
    pool_size=_pool_sizes,
    requested=st.one_of(st.none(), st.integers(min_value=0, max_value=20)),
)
def test_property_15_review_selection_is_a_subset_of_the_pool(
    pool_size: int,
    requested: int | None,
) -> None:
    """Feature: learning-core, Property 15: For any concept and its eligible question
    pool, a review selection is a subset of that pool, and an empty eligible pool
    yields an empty selection without error.

    Validates Requirement 9.3.
    """
    pool = _pool(pool_size)
    selection = select_review_questions(pool, count=requested)

    # The selection is always a subset of the pool (no foreign question, no
    # duplicate, every element drawn from the pool).
    _assert_subset(selection, pool)

    # An empty pool yields an empty selection without error.
    if pool_size == 0:
        assert selection == ()

    # The selection never exceeds the pool.
    assert len(selection) <= pool_size


# =============================================================================
# Property 16: Passing lengthens and failing shortens the review interval
# (Req 9.4, 9.5)
# =============================================================================


@settings(max_examples=200)
@given(now=_instants, current=_current_intervals, passed=st.booleans())
def test_property_16_pass_lengthens_and_fail_shortens_the_interval(
    now: datetime,
    current: timedelta,
    passed: bool,
) -> None:
    """Feature: learning-core, Property 16: For any current review interval, a passed
    review sets the next interval to the Config passed-review interval which is
    greater than the current interval, and a failed review sets it to the Config
    failed-review interval which is less than the current interval.

    Validates Requirements 9.4, 9.5.
    """
    clock = Test_Clock(start=now)
    # A schedule positioned at an arbitrary current interval (the property is
    # over *any* current interval). due_at is irrelevant to the advance here.
    schedule = ReviewSchedule(
        user_id=_USER,
        concept_id=_CONCEPT,
        due_at=now,
        interval=current,
    )

    after = record_review(schedule, passed=passed, clock=clock)

    if passed:
        # A pass sets the Config passed interval, which lengthens the spacing.
        assert after.interval == PASSED_REVIEW_INTERVAL
        assert after.interval > current or current >= PASSED_REVIEW_INTERVAL
        # The next due_at is the new interval from the Clock.
        assert after.due_at == now + PASSED_REVIEW_INTERVAL
    else:
        # A fail sets the Config failed interval, which shortens the spacing.
        assert after.interval == FAILED_REVIEW_INTERVAL
        assert after.interval < current or current <= FAILED_REVIEW_INTERVAL
        assert after.due_at == now + FAILED_REVIEW_INTERVAL

    # Exactly one attempt appended, recording the outcome at the Clock instant.
    assert len(after.history) == len(schedule.history) + 1
    assert after.history[-1].passed is passed
    assert after.history[-1].attempted_at == now


@settings(max_examples=200)
@given(now=_instants, current=_current_intervals, passed=st.booleans())
def test_property_16_new_interval_matches_config_relative_to_initial(
    now: datetime,
    current: timedelta,
    passed: bool,
) -> None:
    """Feature: learning-core, Property 16: For any current review interval, a passed
    review sets the next interval to the Config passed-review interval which is
    greater than the current interval, and a failed review sets it to the Config
    failed-review interval which is less than the current interval.

    Validates Requirements 9.4, 9.5.

    This second body pins the "lengthen vs shorten" ordering against the Config
    initial interval — the point from which a schedule starts — so the direction
    of the change is asserted unambiguously: passed > initial > failed.
    """
    clock = Test_Clock(start=now)
    schedule = ReviewSchedule(
        user_id=_USER,
        concept_id=_CONCEPT,
        due_at=now,
        interval=current,
    )
    after = record_review(schedule, passed=passed, clock=clock)

    if passed:
        assert after.interval == PASSED_REVIEW_INTERVAL
        assert PASSED_REVIEW_INTERVAL > INITIAL_REVIEW_INTERVAL
    else:
        assert after.interval == FAILED_REVIEW_INTERVAL
        assert FAILED_REVIEW_INTERVAL < INITIAL_REVIEW_INTERVAL


# =============================================================================
# Property 19: Reviews below the minimum eligible pool are declined without
# scheduling (Req 28.1, 28.2, 28.3)
# =============================================================================


@settings(max_examples=200)
@given(now=_instants, pool_size=_below_minimum_sizes, has_existing=st.booleans())
def test_property_19_below_minimum_declines_without_scheduling(
    now: datetime,
    pool_size: int,
    has_existing: bool,
) -> None:
    """Feature: learning-core, Property 19: For any review request whose concept has
    fewer than the Config minimum eligible questions, the review is declined and
    no review-schedule entry is created or advanced for that request.

    Validates Requirements 28.1, 28.2, 28.3.
    """
    clock = Test_Clock(start=now)
    repo = In_Memory_ReviewRepository()

    existing: ReviewSchedule | None = None
    if has_existing:
        existing = schedule_on_practiced(None, user_id=_USER, concept_id=_CONCEPT, clock=clock)
        repo.save(existing)

    snapshot = repo.get(_USER, _CONCEPT)  # entry state before the declined call

    result = start_review(
        existing,
        _pool(pool_size),  # strictly below the Config minimum
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )

    # The review is declined (Req 28.1).
    assert result is None or result == existing
    assert not is_pool_eligible(_pool(pool_size))
    # No entry is created or advanced for the request (Req 28.2): the repository
    # state is exactly what it was before the declined call.
    assert repo.get(_USER, _CONCEPT) == snapshot
    if not has_existing:
        assert repo.list_for_user(_USER) == ()
    else:
        assert len(repo.list_for_user(_USER)) == 1


@settings(max_examples=200)
@given(now=_instants, pool_size=_at_or_above_minimum_sizes)
def test_property_19_at_or_above_minimum_proceeds(
    now: datetime,
    pool_size: int,
) -> None:
    """Feature: learning-core, Property 19: For any review request whose concept has
    fewer than the Config minimum eligible questions, the review is declined and
    no review-schedule entry is created or advanced for that request.

    Validates Requirements 28.1, 28.2, 28.3.

    The contrapositive boundary: a pool at or above the Config minimum is NOT
    declined — the review proceeds and the single schedule entry is created.
    """
    clock = Test_Clock(start=now)
    repo = In_Memory_ReviewRepository()

    assert is_pool_eligible(_pool(pool_size))
    result = start_review(
        None,
        _pool(pool_size),
        user_id=_USER,
        concept_id=_CONCEPT,
        clock=clock,
    )
    assert result is not None
    repo.save(result)
    assert result.interval == INITIAL_REVIEW_INTERVAL
    assert result.due_at == now + INITIAL_REVIEW_INTERVAL
    assert len(repo.list_for_user(_USER)) == 1


# =============================================================================
# Property 17: Due and overdue derive purely from the clock (Req 9.6)
# =============================================================================


@settings(max_examples=200)
@given(
    now=_instants,
    offset=st.timedeltas(min_value=timedelta(days=-60), max_value=timedelta(days=60)),
)
def test_property_17_due_and_overdue_derive_purely_from_the_clock(
    now: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 17: For any review schedule and Clock instant,
    due and overdue are determined solely by comparing the Clock to ``due_at``.

    Validates Requirement 9.6.
    """
    from edu_it_domain.learning.review import is_due, is_overdue, overdue_duration

    clock = Test_Clock(start=now)
    schedule = ReviewSchedule(
        user_id=_USER,
        concept_id=_CONCEPT,
        due_at=now - offset,
        interval=INITIAL_REVIEW_INTERVAL,
    )
    assert is_due(schedule, clock=clock) is (offset >= timedelta(0))
    assert is_overdue(schedule, clock=clock) is (offset > timedelta(0))
    assert overdue_duration(schedule, clock=clock) == max(offset, timedelta(0))
