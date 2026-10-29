"""Framework-free learning-domain types (Req 1.1, 5.1, 5.7, 9.1).

These dataclasses are the domain-layer view of the learning tables of
``docs/DATA-MODEL.md`` (D3) — the mastery/review and assessment groups plus the
catalog's ``question_option``. They mirror D3 exactly where it is the
authoritative schema (D3 §7, §9, §10, §14) and carry only the domain-relevant,
API-safe attributes: the internal numeric primary key of a row is never exposed
here, so the domain addresses every content item and record by its immutable
``stable_id`` (D3 §1, Req 1.1) — the one exception being a ``question_option``,
which D3 gives no ``stable_id`` and which is therefore identified by its stable
per-question :attr:`QuestionOption.option_id`.

Like the rest of ``edu_it_domain`` this module imports no web or API framework
(Req 1.8, ``.kiro/steering/structure.md``): it uses only the standard library
(``dataclasses``, ``enum``, ``datetime``, ``typing``). Every type is a frozen,
slotted dataclass so a domain value is immutable once constructed, matching the
convention of the identity ports (``ports/repository.py``). Instants are aware
``datetime`` values read from the injected ``Clock`` (Req 1.3), never the wall
clock.

The services that compute over these types — mastery, decay, review scheduling,
scoring, cooldown, rubric — land in later tasks; this module is the shared
vocabulary they build on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

#: The immutable, opaque public identifier a content item or learner record is
#: addressed by across the domain (D3 §1.2). The domain never sees a row's
#: internal numeric key; it works in ``stable_id`` terms, so this alias names
#: that intent wherever a field holds one.
type StableId = str


class MasteryState(Enum):
    """The mastery state of a concept for one learner (D3 §7, Req 5.2-5.6).

    Mastery is an integer 0-100 per user x concept (Req 5.1); the state is the
    band that integer falls in, gated at the thresholds the domain reads from
    :mod:`edu_it_domain.config`:

    - :attr:`UNSEEN` — the concept has not been encountered yet.
    - :attr:`INTRODUCED` — first encountered with mastery below the Practiced
      threshold (Req 5.2).
    - :attr:`PRACTICED` — mastery in the Practiced band; the state at which a
      concept first enters the spaced-review schedule (Req 5.3, 5.4, 9.1).
    - :attr:`PROFICIENT` — mastery in the Proficient band (Req 5.5).
    - :attr:`MASTERED` — mastery at or above the Mastered threshold with a
      passed review and, where a lab covers the concept, a passed lab check
      (Req 5.6).

    Decay may lower the band of a concept that has reached :attr:`PRACTICED`
    but never past :attr:`PRACTICED` nor below a floor, and never relocks it to
    :attr:`INTRODUCED`/:attr:`UNSEEN` (Req 5.9-5.11).
    """

    UNSEEN = "unseen"
    """The concept has not been encountered by the learner."""

    INTRODUCED = "introduced"
    """First encountered, mastery below the Practiced threshold (Req 5.2)."""

    PRACTICED = "practiced"
    """Mastery in the Practiced band; enters spaced review (Req 5.3, 5.4)."""

    PROFICIENT = "proficient"
    """Mastery in the Proficient band (Req 5.5)."""

    MASTERED = "mastered"
    """Mastery at/above the Mastered threshold with the required gates (Req 5.6)."""


@dataclass(frozen=True, slots=True)
class MasteryEvent:
    """One append-only change to a learner's mastery (D3 §7.1, Req 5.7).

    Every change to a :class:`Mastery` appends exactly one event recording the
    applied delta, the source that caused it, and the instant it occurred as
    read from the injected ``Clock`` (Req 5.7). The ``mastery_event`` table is
    append-only, so an event is an immutable record of history.
    """

    delta: int
    """The signed change applied to the mastery score."""

    source: str
    """What produced the change (e.g. a quiz attempt, a review, decay, a hint)."""

    occurred_at: datetime
    """The instant the change occurred, from the injected Clock (Req 5.7)."""


@dataclass(frozen=True, slots=True)
class Mastery:
    """A learner's mastery of one concept (D3 §7, Req 5.1).

    Mirrors the ``mastery`` row as the domain sees it: the owning learner and
    concept by ``stable_id``, the integer :attr:`score` bounded to 0-100
    (Req 5.1, 5.8), and the derived :attr:`state` band (Req 5.2-5.6). The
    append-only history of changes is held in :attr:`events`, each the delta,
    source, and instant of one change (Req 5.7); it defaults to empty for a
    freshly tracked concept.
    """

    user_id: StableId
    """The ``stable_id`` of the learner the mastery belongs to."""

    concept_id: StableId
    """The ``stable_id`` of the concept the mastery is for."""

    score: int
    """The mastery value, bounded to the inclusive range 0-100 (Req 5.1, 5.8)."""

    state: MasteryState
    """The state band the :attr:`score` falls in (Req 5.2-5.6)."""

    events: tuple[MasteryEvent, ...] = field(default_factory=tuple)
    """The append-only history of changes, oldest first (Req 5.7)."""


@dataclass(frozen=True, slots=True)
class ReviewAttempt:
    """One attempt against a spaced-review schedule entry (D3 §8, Req 9.4, 9.5).

    Mirrors a ``review_attempt`` row: whether the review was :attr:`passed` and
    the instant it was :attr:`attempted_at`, read from the injected ``Clock``. A
    passing review lengthens, and a failing review shortens, the schedule's
    interval (Req 9.4, 9.5), and a passed review is one condition for reaching
    :attr:`MasteryState.MASTERED` (Req 5.6).
    """

    passed: bool
    """Whether this review attempt passed."""

    attempted_at: datetime
    """The instant the review was attempted, from the injected Clock."""


@dataclass(frozen=True, slots=True)
class ReviewSchedule:
    """A concept's spaced-review schedule for one learner (D3 §8, Req 9.1).

    Mirrors a ``review_schedule`` row as the domain sees it. Exactly one entry
    is created when a concept first reaches :attr:`MasteryState.PRACTICED`, and
    no additional entry is created when Practiced is re-reached after a decay
    dip (Req 5.4, 9.1, 9.2). :attr:`due_at` and overdue-ness derive purely from
    the injected ``Clock`` (Req 9.6); :attr:`interval` is the current spacing
    that passing lengthens and failing shortens (Req 9.4, 9.5); :attr:`history`
    is the append-only sequence of attempts against the entry.
    """

    user_id: StableId
    """The ``stable_id`` of the learner the schedule belongs to."""

    concept_id: StableId
    """The ``stable_id`` of the concept under review."""

    due_at: datetime
    """The instant the next review is due, compared against the Clock (Req 9.6)."""

    interval: timedelta
    """The current review spacing; pass lengthens, fail shortens (Req 9.4, 9.5)."""

    history: tuple[ReviewAttempt, ...] = field(default_factory=tuple)
    """The append-only attempts against this schedule entry, oldest first."""


@dataclass(frozen=True, slots=True)
class QuestionOption:
    """One selectable option of a quiz question (D3 §6, Req 3.2).

    Mirrors a ``question_option`` row. D3 gives the option no ``stable_id``, so
    it is identified by its stable per-question :attr:`option_id`; a seeded
    shuffle presents options in a varied order while this identity is what
    scoring maps a chosen position back to (Req 3.2, 3.3). :attr:`is_correct`
    marks whether selecting the option counts as correct.
    """

    option_id: int
    """The option's stable identifier within its question (D3 ``question_option.id``)."""

    is_correct: bool
    """Whether selecting this option is correct."""


@dataclass(frozen=True, slots=True)
class Answer:
    """A single answered question within a quiz attempt (D3 §9, Req 6.1).

    Mirrors an ``answer`` row as the domain sees it: the answered question by
    ``stable_id``, the :attr:`first_answered_at` instant that anchors the
    24-hour anti-farming window, and :attr:`counted_for_mastery` — false for a
    re-answer inside that window so it earns no additional mastery, true at or
    after the boundary under the standard rules (Req 6.1, 6.2, 6.4, 6.5).
    """

    question_id: StableId
    """The ``stable_id`` of the answered question."""

    first_answered_at: datetime
    """The instant the question was first answered; anchors the 24h window (Req 6.4)."""

    counted_for_mastery: bool
    """Whether this answer counts toward mastery gain (Req 6.1, 6.2, 6.5)."""


@dataclass(frozen=True, slots=True)
class QuizAttempt:
    """One attempt at a quiz by a learner (D3 §9, Req 3.1, 3.6).

    Mirrors a ``quiz_attempt`` row as the domain sees it: the attempt and its
    quiz by ``stable_id``, the integer :attr:`score` 0-100 (percentage correct,
    unanswered counting as incorrect; Req 3.1, 3.5), the :attr:`is_best` flag
    marking the highest-scoring attempt with ties broken by recency (Req 3.6,
    6.3), and the :attr:`attempted_at` instant from the injected ``Clock`` that
    feeds the best-attempt tie-break and the cooldown computation.
    """

    stable_id: StableId
    """The attempt's immutable public identifier."""

    quiz_id: StableId
    """The ``stable_id`` of the quiz that was attempted."""

    score: int
    """The attempt score 0-100, percentage of correct answers (Req 3.1, 3.5)."""

    is_best: bool
    """Whether this is the learner's best attempt for the quiz (Req 3.6, 6.3)."""

    attempted_at: datetime
    """The instant of the attempt, from the injected Clock."""

    content_version: str = ""
    """The quiz ``content_version`` the attempt was evaluated under (Req 25.1, 25.2)."""


@dataclass(frozen=True, slots=True)
class RubricCriterion:
    """One weighted criterion of a project rubric (D3 §6, Req 10.1, 10.2).

    Mirrors a ``rubric_criterion`` row as the domain sees it: the criterion by
    ``stable_id`` and its :attr:`max_points`, the weight the criterion
    contributes to the rubric's weighted total against which the pass threshold
    is applied (Req 10.1, 10.2).
    """

    stable_id: StableId
    """The criterion's immutable public identifier."""

    max_points: int
    """The maximum points the criterion contributes (its weight) (Req 10.2)."""


@dataclass(frozen=True, slots=True)
class Rubric:
    """A project's scoring rubric (D3 §6, Req 10.1).

    Mirrors a ``rubric`` row as the domain sees it: the rubric and the project
    it scores by ``stable_id``, with its weighted :attr:`criteria`. A project is
    scored by exactly one rubric; the weighted criteria total is compared
    against the Config pass threshold to decide the outcome (Req 10.1, 10.2,
    10.3).
    """

    stable_id: StableId
    """The rubric's immutable public identifier."""

    project_id: StableId
    """The ``stable_id`` of the project this rubric scores."""

    criteria: tuple[RubricCriterion, ...] = field(default_factory=tuple)
    """The rubric's weighted criteria (Req 10.1, 10.2)."""


@dataclass(frozen=True, slots=True)
class RubricScore:
    """The points awarded for one rubric criterion (D3 §6, Req 10.2).

    Pairs a :class:`RubricCriterion` (by its ``stable_id``) with the
    :attr:`points` awarded for it, bounded by the criterion's ``max_points``.
    The collected scores form the weighted total the domain evaluates against
    the Config pass threshold once the ``ProjectReviewer`` has completed
    (Req 10.2, 10.3, 10.4).
    """

    criterion_id: StableId
    """The ``stable_id`` of the :class:`RubricCriterion` being scored."""

    points: int
    """The points awarded for the criterion (0..``max_points``) (Req 10.2)."""


@dataclass(frozen=True, slots=True)
class ProjectSubmissionRef:
    """A reference to a learner's project submission (D3 §9, Req 10.4).

    Mirrors a ``project_submission`` row as the domain sees it: the submission
    and its project by ``stable_id``, and the :attr:`review_outcome` from the
    ``ProjectReviewer`` extension point — ``None`` while the review is still
    pending, so the domain exposes no pass/fail outcome until the reviewer
    completes (Req 10.4, 10.5).
    """

    stable_id: StableId
    """The submission's immutable public identifier."""

    project_id: StableId
    """The ``stable_id`` of the submitted project."""

    review_outcome: str | None = None
    """The reviewer's outcome, or ``None`` while the review is pending (Req 10.4)."""


__all__ = [
    "Answer",
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
]
