"""Quiz scoring, deterministic option shuffle, and best-attempt selection (Req 3, 6.3).

This pure, framework-free domain service owns the assessment rules of the
learning engine: it scores a quiz attempt as the percentage of questions
answered correctly (an unanswered question counts as incorrect), rejects an
attempt whose selections reference options that do not belong to their
question, shuffles a question's options deterministically from a seed, maps a
selected option back to its original identity to judge it, and selects the best
attempt (highest score, ties broken in favor of the most recent). Like the rest
of ``edu_it_domain`` it imports no web or API framework (Req 1.8,
``.kiro/steering/structure.md``) and uses only the standard library.

There are no scoring-specific business numbers. The 0-100 bounds it shares with
mastery are read from :mod:`edu_it_domain.config`
(:data:`~edu_it_domain.config.MASTERY_MIN` / :data:`~edu_it_domain.config.MASTERY_MAX`),
never hardcoded here (Req 1.3).

The domain rules it encodes (Req 3, 6.3):

- **Percent-correct scoring (Req 3.1, 3.5).** The score is the integer
  percentage of the quiz's questions answered correctly, rounded with
  :func:`round`, in the inclusive range ``MASTERY_MIN``-``MASTERY_MAX``. Every
  question type, including command-output, is judged by the same
  :attr:`~edu_it_domain.learning.models.QuestionOption.is_correct` mechanism. An
  empty quiz scores ``MASTERY_MIN``.
- **Foreign options (Req 3.4).** A selection naming an option that is not one of
  its question's options, or a question that is not part of the quiz, raises
  :class:`ForeignOptionError`; the caller persists nothing for a rejected
  attempt. An explicit ``None`` is an unanswered question, not a foreign one.
- **Deterministic shuffle (Req 3.2, 3.3).** :func:`shuffle_options` returns a
  seeded permutation of the options; the same seed and option set always give
  the same order and no option is added, removed, or duplicated. Correctness is
  keyed on the original option identity, so shuffling never changes which answer
  is correct.
- **Best attempt (Req 3.6, 6.3).** The best attempt has the highest ``score``;
  ties go to the most recent ``attempted_at``.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import replace

from edu_it_domain.config import MASTERY_MAX, MASTERY_MIN
from edu_it_domain.learning.models import QuestionOption, QuizAttempt, StableId
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord

__all__ = [
    "ForeignOptionError",
    "is_selection_correct",
    "mark_best",
    "score_attempt",
    "select_best",
    "shuffle_options",
]


class ForeignOptionError(ValueError):
    """Raised when a selection references an option foreign to its question (Req 3.4).

    The attempt is rejected: the caller persists no ``quiz_attempt`` or
    ``answer`` for it.
    """


def is_selection_correct(question: QuestionRecord, option_id: int) -> bool:
    """Return whether ``option_id`` is the correct option of ``question`` (Req 3.3).

    The verdict depends only on the original option identity within
    ``question``, so it is unaffected by any shuffled presentation order.

    Raises:
        ForeignOptionError: ``option_id`` is not an option of ``question``.
    """
    for option in question.options:
        if option.option_id == option_id:
            return option.is_correct
    raise ForeignOptionError(
        f"option {option_id} does not belong to question {question.stable_id!r}"
    )


def score_attempt(quiz: QuizRecord, selections: Mapping[StableId, int | None]) -> int:
    """Return the 0-100 percent-correct score of ``selections`` against ``quiz`` (Req 3.1, 3.5).

    ``selections`` maps a question ``stable_id`` to the selected ``option_id``,
    or ``None`` for an unanswered question; a question absent from the mapping is
    likewise unanswered and counts as incorrect. An empty quiz scores
    :data:`~edu_it_domain.config.MASTERY_MIN`.

    Raises:
        ForeignOptionError: a selection names an option outside its question, or
            a question that is not part of ``quiz`` (Req 3.4).
    """
    questions = {question.stable_id: question for question in quiz.questions}

    # Validate every selection first so a foreign reference rejects the whole attempt.
    correct = 0
    for question_id, option_id in selections.items():
        question = questions.get(question_id)
        if question is None:
            raise ForeignOptionError(
                f"question {question_id!r} is not part of quiz {quiz.stable_id!r}"
            )
        if option_id is not None and is_selection_correct(question, option_id):
            correct += 1

    total = len(quiz.questions)
    if total == 0:
        return MASTERY_MIN
    percent = round(correct * MASTERY_MAX / total)
    return min(MASTERY_MAX, max(MASTERY_MIN, percent))


def shuffle_options(options: Sequence[QuestionOption], seed: int) -> tuple[QuestionOption, ...]:
    """Return a deterministic seeded permutation of ``options`` (Req 3.2).

    The same ``seed`` and option set always yield the same order, and the result
    is a permutation of the input: nothing is added, removed, or duplicated.
    """
    shuffled = list(options)
    random.Random(seed).shuffle(shuffled)
    return tuple(shuffled)


def select_best(attempts: Sequence[QuizAttempt]) -> QuizAttempt | None:
    """Return the best attempt, or ``None`` when there are none (Req 3.6, 6.3).

    The best attempt has the highest ``score``; ties go to the most recent
    ``attempted_at``.
    """
    if not attempts:
        return None
    return max(attempts, key=lambda attempt: (attempt.score, attempt.attempted_at))


def mark_best(attempts: Sequence[QuizAttempt]) -> tuple[QuizAttempt, ...]:
    """Return ``attempts`` with exactly the best one flagged ``is_best`` (Req 3.6, 6.3).

    Every other attempt's ``is_best`` is cleared. Order is preserved; an empty
    input yields an empty result. The inputs are left unchanged.
    """
    best = select_best(attempts)
    return tuple(replace(attempt, is_best=attempt is best) for attempt in attempts)
