"""Framework-free learning-domain types and services (Req 1.1).

The learning engine lives here as pure, framework-free Python: dataclasses that
mirror the ``docs/DATA-MODEL.md`` (D3) catalog, progress, mastery/review, and
assessment tables, and the services that compute mastery, decay, review
scheduling, scoring, cooldown, and rubric outcomes. Like the rest of
``edu_it_domain`` this package imports no web or API framework (Req 1.8,
``.kiro/steering/structure.md``) and reads time only through the injected
``Clock`` (Req 1.3).

This phase introduces the domain *types* (:mod:`edu_it_domain.learning.models`)
and the first domain services: :mod:`edu_it_domain.learning.unlocking` (module
unlocking and lesson-completion recording, Req 2) and
:mod:`edu_it_domain.learning.mastery` (mastery deltas, state bands, the Mastered
gate, and decay, Req 5), and :mod:`edu_it_domain.learning.cooldown` (quiz-attempt
cooldown, Req 4), and :mod:`edu_it_domain.learning.antifarming` (the 24-hour
re-answer window, Req 6); the remaining services land in later tasks.
"""

from __future__ import annotations

from edu_it_domain.learning.antifarming import (
    counts_for_mastery,
    evaluate_reanswer,
)
from edu_it_domain.learning.cooldown import (
    attemptable_at,
    is_in_cooldown,
)
from edu_it_domain.learning.hints import (
    HINT_SOURCE,
    HintReveal,
    record_hint_reveal,
    tempered_gain,
)
from edu_it_domain.learning.mastery import (
    apply_decay,
    apply_delta,
    derive_state,
)
from edu_it_domain.learning.models import (
    Answer,
    Mastery,
    MasteryEvent,
    MasteryState,
    ProjectSubmissionRef,
    QuestionOption,
    QuizAttempt,
    ReviewAttempt,
    ReviewSchedule,
    Rubric,
    RubricCriterion,
    RubricScore,
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
from edu_it_domain.learning.unlocking import (
    REQUIRED_PREREQUISITE_STATE,
    is_module_unlocked,
    module_lock_state,
    prerequisite_met,
    record_lesson_completion,
)

__all__ = [
    "HINT_SOURCE",
    "REQUIRED_PREREQUISITE_STATE",
    "Answer",
    "ForeignOptionError",
    "HintReveal",
    "Mastery",
    "MasteryEvent",
    "MasteryState",
    "ProjectSubmissionRef",
    "QuestionOption",
    "QuizAttempt",
    "ReviewAttempt",
    "ReviewSchedule",
    "Rubric",
    "RubricCriterion",
    "RubricScore",
    "StableId",
    "apply_decay",
    "apply_delta",
    "attemptable_at",
    "counts_for_mastery",
    "derive_state",
    "evaluate_reanswer",
    "is_in_cooldown",
    "is_module_unlocked",
    "is_selection_correct",
    "mark_best",
    "module_lock_state",
    "prerequisite_met",
    "record_hint_reveal",
    "record_lesson_completion",
    "score_attempt",
    "select_best",
    "shuffle_options",
    "tempered_gain",
]
