"""Rules-based + property tests for scoring/shuffle/matching (task 4.1, Req 3).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.scoring`
does not exist yet (it lands in task 4.2). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.scoring` (task 4.2)
-----------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Score a quiz attempt 0-100 (unanswered = incorrect);
deterministic option shuffle by seed; map shuffled position back to original
option identity; reject answers referencing foreign options; mixed types incl.
command-output; best-attempt selection (tie -> most recent)"). There are no
scoring-specific business numbers; the 0-100 bounds it shares with mastery live
in :mod:`edu_it_domain.config` (``MASTERY_MIN`` / ``MASTERY_MAX``) and are read
from there, never hardcoded (Req 1.3). These tests target this surface:

``score_attempt(quiz, selections) -> int``
    Score a :class:`~edu_it_domain.ports.catalog.QuizRecord` against a mapping
    of question ``stable_id`` to the learner's selected ``option_id`` (or
    ``None`` for an unanswered question, and questions absent from the mapping
    are likewise unanswered). Returns an integer in the inclusive 0-100 range
    equal to the percentage of the quiz's questions answered correctly against
    each question's :attr:`QuestionOption.is_correct`, counting any unanswered
    question as incorrect (Req 3.1, 3.5). Mixed question types — including a
    command-output type — are all judged by the same ``is_correct`` mechanism
    (Req 3.5). An empty quiz scores 0 (no questions correct).

``ForeignOptionError``
    A domain error raised by :func:`score_attempt` when a selection references
    an ``option_id`` that does not belong to its question. The attempt is
    rejected — the caller persists no ``quiz_attempt`` or ``answer`` for it
    (Req 3.4) — so the service signals rejection rather than silently scoring
    the foreign option as incorrect.

``shuffle_options(options, seed) -> tuple[QuestionOption, ...]``
    A deterministic seeded permutation of a question's options: the same
    ``seed`` and the same option set always yield the same order (Req 3.2), and
    the result is a permutation of the input — no option added, removed, or
    duplicated (Req 3.2). Shuffling reorders but preserves option identity.

``is_selection_correct(question, option_id) -> bool``
    Map a selected ``option_id`` back to its original
    :class:`~edu_it_domain.learning.models.QuestionOption` identity within the
    ``question`` and return that option's ``is_correct`` verdict (Req 3.3). The
    verdict depends only on the original option identity, so judging a position
    after a shuffle — once mapped back to its option — yields the same verdict
    as judging the originally-identified option: shuffling never changes which
    answer is correct (Req 3.3).

``mark_best(attempts) -> tuple[QuizAttempt, ...]``
    Return the attempts with exactly one marked ``is_best`` — the highest
    ``score``, ties broken in favor of the most recent ``attempted_at`` — and
    every other attempt's ``is_best`` cleared to false (Req 3.6, 6.3). An empty
    input yields an empty result.

``select_best(attempts) -> QuizAttempt | None``
    Return the single best attempt by the same rule (highest score, tie ->
    most recent ``attempted_at``), or ``None`` for an empty input. Completion is
    evaluated against this best attempt regardless of the 24-hour anti-farming
    window or idempotency dedupe (Req 3.6, 6.3).

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock` and the in-memory learning repositories — wired
exactly as the other fake-driven tests wire them: no database and no wall clock
(Req 11.4). Quiz shape is built with the catalog port records
(:class:`QuizRecord`, :class:`QuestionRecord`, :class:`QuestionOption`) the
scoring service reads over.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import MASTERY_MAX, MASTERY_MIN
from edu_it_domain.learning.models import (
    QuestionOption,
    QuizAttempt,
    StableId,
)
from edu_it_domain.learning.scoring import (
    ForeignOptionError,
    is_selection_correct,
    mark_best,
    score_attempt,
    select_best,
    shuffle_options,
)
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_QUIZ: StableId = "qz_1"
_LESSON: StableId = "ls_1"
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _question(
    stable_id: StableId,
    *,
    correct_option_id: int,
    option_ids: tuple[int, ...] = (1, 2, 3, 4),
    concept_ids: tuple[StableId, ...] = ("cn_1",),
) -> QuestionRecord:
    """Build a single-correct-answer question whose correct option is ``correct_option_id``.

    Exactly one option carries ``is_correct=True`` (the one whose ``option_id``
    is ``correct_option_id``); the rest are distractors. ``correct_option_id``
    must be one of ``option_ids``.
    """
    assert correct_option_id in option_ids
    return QuestionRecord(
        stable_id=stable_id,
        options=tuple(
            QuestionOption(option_id=oid, is_correct=(oid == correct_option_id))
            for oid in option_ids
        ),
        concept_ids=concept_ids,
    )


def _quiz(*questions: QuestionRecord) -> QuizRecord:
    """Build a quiz over ``questions``."""
    return QuizRecord(stable_id=_QUIZ, lesson_id=_LESSON, questions=questions)


def _attempt(stable_id: StableId, score: int, *, offset: timedelta) -> QuizAttempt:
    """Build a quiz attempt scoring ``score`` at ``_START + offset``.

    ``is_best`` starts false; :func:`mark_best` / :func:`select_best` decide it.
    """
    return QuizAttempt(
        stable_id=stable_id,
        quiz_id=_QUIZ,
        score=score,
        is_best=False,
        attempted_at=_START + offset,
    )


def _expected_percent(correct: int, total: int) -> int:
    """Return the integer 0-100 percentage of ``correct`` out of ``total`` questions.

    An empty quiz (``total == 0``) scores 0 — no question was answered
    correctly. The percentage is rounded the way the service rounds (banker's
    rounding via :func:`round`), computed here from the same inputs rather than
    hardcoding any expected number.
    """
    if total == 0:
        return 0
    return round(correct * 100 / total)


# --- Generators constrained to the input space -------------------------------

#: Option-id pools of 2-6 distinct ids, drawn from a modest range so a question
#: offers a realistic set of choices without exploding the search space.
_option_id_pools = st.lists(
    st.integers(min_value=1, max_value=12),
    min_size=2,
    max_size=6,
    unique=True,
).map(tuple)

#: Shuffle seeds spanning a wide integer range, including 0 and large values.
_seeds = st.integers(min_value=0, max_value=2**31 - 1)

#: Attempt scores across the full 0-100 scale (what a scored attempt holds).
_scores = st.integers(min_value=MASTERY_MIN, max_value=MASTERY_MAX)


def _question_pool(ids_and_correct: list[tuple[int, int]]) -> list[QuestionRecord]:
    """Build a list of questions from (correct_option_id, n_distractors) pairs.

    Each pair yields a question with a distinct ``stable_id`` whose option-id
    pool is ``correct_option_id`` plus ``n_distractors`` further ids.
    """
    questions: list[QuestionRecord] = []
    for i, (correct, extra) in enumerate(ids_and_correct):
        option_ids = (correct, *range(correct + 1, correct + 1 + extra))
        questions.append(_question(f"qn_{i}", correct_option_id=correct, option_ids=option_ids))
    return questions


# =============================================================================
# Rules-based tests — percent-correct scoring (Req 3.1, 3.5)
# =============================================================================


def test_all_correct_scores_100() -> None:
    # Req 3.1: a fully-correct attempt scores 100 (100% of questions correct).
    quiz = _quiz(
        _question("qn_0", correct_option_id=1),
        _question("qn_1", correct_option_id=2),
        _question("qn_2", correct_option_id=3),
        _question("qn_3", correct_option_id=4),
    )
    selections = {"qn_0": 1, "qn_1": 2, "qn_2": 3, "qn_3": 4}
    assert score_attempt(quiz, selections) == MASTERY_MAX


def test_all_incorrect_scores_0() -> None:
    # Req 3.1: selecting a wrong (existing) option for every question scores 0.
    quiz = _quiz(
        _question("qn_0", correct_option_id=1),
        _question("qn_1", correct_option_id=2),
    )
    selections = {"qn_0": 2, "qn_1": 1}  # both wrong but valid options
    assert score_attempt(quiz, selections) == MASTERY_MIN


def test_half_correct_scores_50() -> None:
    # Req 3.1: two of four correct is 50%.
    quiz = _quiz(
        _question("qn_0", correct_option_id=1),
        _question("qn_1", correct_option_id=2),
        _question("qn_2", correct_option_id=3),
        _question("qn_3", correct_option_id=4),
    )
    # qn_0, qn_1 correct; qn_2 selects option 1 (wrong, correct is 3); qn_3
    # selects option 1 (wrong, correct is 4) -> two of four correct.
    selections = {"qn_0": 1, "qn_1": 2, "qn_2": 1, "qn_3": 1}
    assert score_attempt(quiz, selections) == 50


def test_unanswered_question_counts_as_incorrect() -> None:
    # Req 3.1 / 3.5: an unanswered question (None, or absent from the mapping)
    # counts as incorrect. Two questions, one correct and one unanswered -> 50%.
    quiz = _quiz(
        _question("qn_0", correct_option_id=1),
        _question("qn_1", correct_option_id=2),
    )
    assert score_attempt(quiz, {"qn_0": 1, "qn_1": None}) == 50
    assert score_attempt(quiz, {"qn_0": 1}) == 50  # qn_1 absent == unanswered


def test_all_unanswered_scores_0() -> None:
    # Req 3.1: a quiz with no answers at all scores 0.
    quiz = _quiz(
        _question("qn_0", correct_option_id=1),
        _question("qn_1", correct_option_id=2),
    )
    assert score_attempt(quiz, {}) == MASTERY_MIN


def test_empty_quiz_scores_0() -> None:
    # A quiz with no questions scores 0 — no question was answered correctly.
    assert score_attempt(_quiz(), {}) == MASTERY_MIN


def test_mixed_types_including_command_output_scored_by_is_correct() -> None:
    # Req 3.5: mixed question types, including a command-output question, are
    # each judged against their options' is_correct. The command-output question
    # (qn_cmd) models its expected-output choice as the is_correct option; the
    # domain scores it by the same mechanism as a multiple-choice question.
    mcq = _question("qn_mcq", correct_option_id=2, option_ids=(1, 2, 3))
    true_false = _question("qn_tf", correct_option_id=1, option_ids=(1, 2))
    command_output = _question("qn_cmd", correct_option_id=7, option_ids=(7, 8, 9))
    quiz = _quiz(mcq, true_false, command_output)

    # Correct MCQ + correct command-output, wrong true/false -> 2 of 3.
    selections = {"qn_mcq": 2, "qn_tf": 2, "qn_cmd": 7}
    assert score_attempt(quiz, selections) == _expected_percent(2, 3)


# =============================================================================
# Rules-based tests — foreign-option rejection (Req 3.4)
# =============================================================================


def test_foreign_option_selection_rejects_the_attempt() -> None:
    # Req 3.4: a selection referencing an option that does not belong to its
    # question rejects the attempt (raises a domain error) rather than scoring
    # it. The caller persists no quiz_attempt/answer for a rejected attempt.
    quiz = _quiz(_question("qn_0", correct_option_id=1, option_ids=(1, 2, 3)))
    with pytest.raises(ForeignOptionError):
        score_attempt(quiz, {"qn_0": 999})  # 999 is not an option of qn_0


def test_foreign_option_for_an_unknown_question_rejects_the_attempt() -> None:
    # Req 3.4: a selection for a question that is not part of the quiz is also a
    # foreign reference and rejects the attempt.
    quiz = _quiz(_question("qn_0", correct_option_id=1, option_ids=(1, 2)))
    with pytest.raises(ForeignOptionError):
        score_attempt(quiz, {"qn_not_in_quiz": 1})


def test_none_selection_is_not_a_foreign_option() -> None:
    # Req 3.1 vs 3.4: an explicit None (unanswered) is incorrect, not foreign —
    # it must not raise. Only a concrete option id outside the question's set is
    # a foreign reference.
    quiz = _quiz(_question("qn_0", correct_option_id=1, option_ids=(1, 2)))
    assert score_attempt(quiz, {"qn_0": None}) == MASTERY_MIN


# =============================================================================
# Rules-based tests — option shuffle (Req 3.2, 3.3)
# =============================================================================


def test_shuffle_is_deterministic_for_the_same_seed() -> None:
    # Req 3.2: the same seed and the same option set always produce the same
    # order.
    options = tuple(QuestionOption(option_id=i, is_correct=(i == 1)) for i in range(1, 6))
    assert shuffle_options(options, seed=42) == shuffle_options(options, seed=42)


def test_shuffle_is_a_permutation_adding_removing_duplicating_nothing() -> None:
    # Req 3.2: the shuffle neither adds, removes, nor duplicates any option.
    options = tuple(QuestionOption(option_id=i, is_correct=(i == 3)) for i in range(1, 6))
    shuffled = shuffle_options(options, seed=7)
    assert len(shuffled) == len(options)
    assert {o.option_id for o in shuffled} == {o.option_id for o in options}
    assert sorted(o.option_id for o in shuffled) == sorted(o.option_id for o in options)


def test_shuffle_preserves_correctness_mapping() -> None:
    # Req 3.3: judging a selected position after mapping it back to its original
    # option identity yields the same verdict. The correct option stays the
    # correct one wherever it lands in the shuffled order.
    options = tuple(QuestionOption(option_id=i, is_correct=(i == 4)) for i in range(1, 6))
    question = QuestionRecord(stable_id="qn_0", options=options, concept_ids=("cn_1",))
    shuffled = shuffle_options(options, seed=123)

    for position in shuffled:
        # Mapping the shuffled position's option id back to the question's
        # identity gives the same is_correct verdict as the option itself.
        assert is_selection_correct(question, position.option_id) is position.is_correct

    # Exactly the original correct option (id 4) is judged correct after shuffle.
    assert is_selection_correct(question, 4) is True
    assert sum(1 for o in shuffled if is_selection_correct(question, o.option_id)) == 1


# =============================================================================
# Rules-based tests — best-attempt selection (Req 3.6, 6.3)
# =============================================================================


def test_best_attempt_is_the_highest_score() -> None:
    # Req 3.6: the highest-scoring attempt is marked is_best.
    attempts = (
        _attempt("a1", 40, offset=timedelta()),
        _attempt("a2", 90, offset=timedelta(hours=1)),
        _attempt("a3", 70, offset=timedelta(hours=2)),
    )
    best = select_best(attempts)
    assert best is not None
    assert best.stable_id == "a2"

    marked = mark_best(attempts)
    assert tuple(a.is_best for a in marked) == (False, True, False)


def test_best_attempt_tie_breaks_to_the_most_recent() -> None:
    # Req 3.6: ties on score break in favor of the most recent attempt.
    attempts = (
        _attempt("a1", 80, offset=timedelta()),
        _attempt("a2", 80, offset=timedelta(hours=2)),  # same score, later
        _attempt("a3", 80, offset=timedelta(hours=1)),
    )
    best = select_best(attempts)
    assert best is not None
    assert best.stable_id == "a2"  # the latest of the three tied attempts


def test_mark_best_marks_exactly_one_and_clears_the_rest() -> None:
    # Req 3.6: marking produces exactly one is_best attempt; all others false.
    attempts = (
        _attempt("a1", 55, offset=timedelta()),
        _attempt("a2", 55, offset=timedelta(hours=1)),
        _attempt("a3", 30, offset=timedelta(hours=2)),
    )
    marked = mark_best(attempts)
    assert sum(1 for a in marked if a.is_best) == 1
    # The tie (a1, a2 at 55) resolves to the most recent, a2.
    assert next(a for a in marked if a.is_best).stable_id == "a2"


def test_select_best_of_empty_is_none() -> None:
    # No attempts -> no best attempt.
    assert select_best(()) is None
    assert mark_best(()) == ()


# =============================================================================
# Property 3: Quiz score equals the percentage of correct answers
# (Req 3.1, 3.5)
# =============================================================================


@settings(max_examples=200)
@given(
    specs=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=20),  # correct option id
            st.integers(min_value=1, max_value=4),  # number of distractors
        ),
        min_size=0,
        max_size=10,
    ),
    data=st.data(),
)
def test_property_3_score_equals_percentage_correct(
    specs: list[tuple[int, int]],
    data: st.DataObject,
) -> None:
    """Feature: learning-core, Property 3: Quiz score equals the percentage of correct answers.

    For any quiz and any (possibly partial) set of submitted answers, the score
    is an integer in 0-100 equal to the percentage of the quiz's questions
    answered correctly against each question's question_option.is_correct,
    counting every unanswered question as incorrect (Req 3.1, 3.5).
    """
    questions = _question_pool(specs)
    quiz = _quiz(*questions)

    # For each question choose: unanswered (None), the correct option, or a
    # wrong (but valid) option. Count the ones we expect scored correct.
    selections: dict[StableId, int | None] = {}
    expected_correct = 0
    for question in questions:
        correct_id = next(o.option_id for o in question.options if o.is_correct)
        choice = data.draw(st.sampled_from(("unanswered", "correct", "wrong")))
        if choice == "unanswered":
            selections[question.stable_id] = None
        elif choice == "correct":
            selections[question.stable_id] = correct_id
            expected_correct += 1
        else:  # a valid but incorrect option (if the question has one)
            wrong = [o.option_id for o in question.options if not o.is_correct]
            if wrong:
                selections[question.stable_id] = data.draw(st.sampled_from(wrong))
            else:  # single-option question: only the correct one exists
                selections[question.stable_id] = correct_id
                expected_correct += 1

    score = score_attempt(quiz, selections)

    assert isinstance(score, int)
    assert MASTERY_MIN <= score <= MASTERY_MAX
    assert score == _expected_percent(expected_correct, len(questions))


# =============================================================================
# Property 4: Option shuffle is a deterministic permutation preserving
# correctness (Req 3.2, 3.3)
# =============================================================================


@settings(max_examples=200)
@given(option_ids=_option_id_pools, seed=_seeds, data=st.data())
def test_property_4_shuffle_is_deterministic_permutation_preserving_correctness(
    option_ids: tuple[int, ...],
    seed: int,
    data: st.DataObject,
) -> None:
    """Feature: learning-core, Property 4: Option shuffle is a deterministic permutation
    preserving correctness.

    For any option set and any seed, shuffling produces the same order for the
    same seed and option set, the result is a permutation of the input (no
    option added, removed, or duplicated), and judging a selected position after
    mapping it back to its original question_option identity yields the same
    correctness verdict as judging the originally-identified option (Req 3.2,
    3.3).
    """
    # Exactly one option in the set is the correct one.
    correct_index = data.draw(st.integers(min_value=0, max_value=len(option_ids) - 1))
    options = tuple(
        QuestionOption(option_id=oid, is_correct=(i == correct_index))
        for i, oid in enumerate(option_ids)
    )
    question = QuestionRecord(stable_id="qn_0", options=options, concept_ids=("cn_1",))

    first = shuffle_options(options, seed=seed)
    second = shuffle_options(options, seed=seed)

    # Determinism: same seed + same option set -> identical order (Req 3.2).
    assert first == second

    # Permutation invariant: same multiset of option ids, no dupes, same length
    # (nothing added, removed, or duplicated) (Req 3.2).
    assert len(first) == len(options)
    assert sorted(o.option_id for o in first) == sorted(o.option_id for o in options)
    assert len({o.option_id for o in first}) == len(first)

    # Correctness preservation: mapping each shuffled position back to the
    # original option identity yields the same verdict as the option itself, so
    # exactly the one originally-correct option id is still judged correct
    # wherever it landed (Req 3.3).
    correct_id = option_ids[correct_index]
    for position in first:
        expected = position.option_id == correct_id
        assert is_selection_correct(question, position.option_id) is expected
    assert sum(1 for o in first if is_selection_correct(question, o.option_id)) == 1


# =============================================================================
# Property 5: Best attempt is the highest score, ties broken by recency
# (Req 3.6, 6.3)
# =============================================================================


@settings(max_examples=200)
@given(
    attempts_spec=st.lists(
        st.tuples(_scores, st.integers(min_value=0, max_value=1000)),
        min_size=1,
        max_size=12,
    ),
    now=st.datetimes(
        min_value=datetime(2000, 1, 1, 0, 0, 0),
        max_value=datetime(2100, 1, 1, 0, 0, 0),
    ).map(lambda naive: naive.replace(tzinfo=UTC)),
)
def test_property_5_best_attempt_is_highest_score_ties_by_recency(
    attempts_spec: list[tuple[int, int]],
    now: datetime,
) -> None:
    """Feature: learning-core, Property 5: Best attempt is the highest score, ties
    broken by recency.

    For any set of quiz attempts, the attempt marked is_best has the maximum
    score, with ties broken in favor of the most recent attempt, and completion
    is evaluated against that attempt regardless of the 24-hour window or
    idempotency dedupe (Req 3.6, 6.3).
    """
    clock = Test_Clock(start=now)
    base = clock.now()

    # Build attempts with distinct instants so "most recent" is unambiguous: the
    # drawn offset sets the coarse ordering and the index breaks any offset tie,
    # so no two attempts share an attempted_at. Distinct stable ids keep them
    # individually addressable.
    attempts = tuple(
        QuizAttempt(
            stable_id=f"a{i}",
            quiz_id=_QUIZ,
            score=score,
            is_best=False,
            attempted_at=base + timedelta(minutes=offset * len(attempts_spec) + i),
        )
        for i, (score, offset) in enumerate(attempts_spec)
    )

    best = select_best(attempts)
    assert best is not None

    # The best score is the maximum across all attempts.
    max_score = max(a.score for a in attempts)
    assert best.score == max_score

    # Among all attempts sharing the maximum score, the best is the most recent.
    top = [a for a in attempts if a.score == max_score]
    most_recent_top = max(top, key=lambda a: a.attempted_at)
    assert best.stable_id == most_recent_top.stable_id
    assert best.attempted_at == most_recent_top.attempted_at

    # mark_best agrees with select_best: exactly one is_best, and it is the same
    # attempt; every other attempt's is_best is cleared.
    marked = mark_best(attempts)
    assert len(marked) == len(attempts)
    best_marked = [a for a in marked if a.is_best]
    assert len(best_marked) == 1
    assert best_marked[0].stable_id == best.stable_id
    # Identity of the set is preserved — same stable ids, just re-flagged.
    assert {a.stable_id for a in marked} == {a.stable_id for a in attempts}
