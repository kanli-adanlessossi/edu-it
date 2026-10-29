"""The catalog repository port (Req 1.2, D3 §6).

The authored learning content — the catalog group of ``docs/DATA-MODEL.md`` (D3
§6): tracks, modules, lessons, their quizzes/questions/options, projects with
rubrics, concepts, and the prerequisite edges between concepts — is read through
the port declared here. The domain services (unlocking, scoring, review
selection, rubric) need the *shape* of that content to compute over it: which
concepts a module's prerequisites require, which options a question offers and
which are correct, which criteria a project's rubric weighs. This port is the
read contract for that catalog shape.

Like the identity repositories (ADR 0004) and the sibling learning ports, this
is a framework-free :class:`typing.Protocol` (``@runtime_checkable``, standard
library + domain imports only, no web/API framework per Req 1.8). The
application layer depends on the protocol; the infrastructure layer supplies a
PostgreSQL (SQLAlchemy Core) implementation and, for tests, an in-memory one.
Every content item is addressed by its immutable, opaque ``stable_id`` (D3 §1.2,
Req 1.1); the internal numeric primary key of a row is never exposed. A
``question_option`` carries no ``stable_id`` in D3, so it is addressed by its
stable per-question ``option_id``, matching
:class:`~edu_it_domain.learning.models.QuestionOption`.

Scope note: content *authoring* and the idempotent ``content sync`` upsert
(Milestone B) write the catalog; this read port is what the Milestone A domain
services and the Milestone C read endpoints consume. Writing the catalog is the
concern of the content pipeline, not this learner-facing read port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from edu_it_domain.learning.models import (
    QuestionOption,
    Rubric,
    StableId,
)

__all__ = [
    "CatalogRepository",
    "ConceptRecord",
    "ModuleRecord",
    "QuestionRecord",
    "QuizRecord",
]


@dataclass(frozen=True, slots=True)
class ConceptRecord:
    """A concept and the concepts it requires (D3 §6, Req 2.1).

    Mirrors a ``concept`` row together with its outgoing ``prerequisite`` edges
    as the domain sees them: the concept by :attr:`stable_id` and the
    ``stable_id`` of each concept it directly :attr:`requires` (D3 §6,
    ``concept ||--o{ prerequisite``). The unlocking service reads these edges to
    decide a module's lock state from its prerequisite concepts' mastery
    (Req 2.1, 2.2); the ``content validate`` command checks the edge set for
    acyclicity (Req 13.4).
    """

    stable_id: StableId
    """The concept's immutable public identifier."""

    requires: tuple[StableId, ...] = field(default_factory=tuple)
    """The ``stable_id`` of each directly required concept (D3 ``prerequisite``)."""


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    """A quiz question and its options (D3 §6, Req 3.1, 3.2).

    Mirrors a ``question`` row with its ``question_option`` children as the
    domain sees them: the question by :attr:`stable_id`, the concepts it
    :attr:`covers` by ``stable_id`` (``question }o--o{ concept``), and its
    :attr:`options`. Scoring reads the options to judge an answer and to map a
    shuffled position back to the original
    :class:`~edu_it_domain.learning.models.QuestionOption` identity (Req 3.2,
    3.3); an option carries no ``stable_id`` so it is addressed by its
    per-question ``option_id``.
    """

    stable_id: StableId
    """The question's immutable public identifier."""

    options: tuple[QuestionOption, ...] = field(default_factory=tuple)
    """The selectable options of the question (D3 ``question_option``)."""

    concept_ids: tuple[StableId, ...] = field(default_factory=tuple)
    """The ``stable_id`` of each concept the question covers (D3 ``question }o--o{ concept``)."""


@dataclass(frozen=True, slots=True)
class QuizRecord:
    """A quiz and its questions (D3 §6, Req 3.1, 9.3).

    Mirrors a ``quiz`` row with its ``question`` children as the domain sees
    them: the quiz and the lesson it assesses by :attr:`stable_id`, and its
    :attr:`questions`. Scoring reads the questions to compute the percentage
    correct (Req 3.1, 3.5); review selection draws its eligible pool from the
    quiz's questions (Req 9.3).
    """

    stable_id: StableId
    """The quiz's immutable public identifier."""

    lesson_id: StableId
    """The ``stable_id`` of the lesson this quiz assesses."""

    questions: tuple[QuestionRecord, ...] = field(default_factory=tuple)
    """The quiz's questions (D3 ``quiz ||--o{ question``)."""

    content_version: str = ""
    """The content hash of this quiz revision; bumped by ``content sync`` (Req 25, D3 §10)."""


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """A module and the concepts gating its unlock (D3 §6, Req 2.1, 2.2).

    Mirrors a ``module`` row as the domain sees it: the module and the track it
    belongs to by :attr:`stable_id`, its :attr:`ordinal` within the track, and
    the ``stable_id`` of each concept whose mastery gates the module's unlock
    (:attr:`prerequisite_concept_ids`). The unlocking service reads these to lock
    a module while a prerequisite is unmet and unlock it when all are met
    (Req 2.1, 2.2).
    """

    stable_id: StableId
    """The module's immutable public identifier."""

    track_id: StableId
    """The ``stable_id`` of the track that contains the module."""

    ordinal: int
    """The module's position within its track (D3 ``module.ordinal``)."""

    prerequisite_concept_ids: tuple[StableId, ...] = field(default_factory=tuple)
    """The ``stable_id`` of each concept whose mastery gates the module (Req 2.1)."""


@runtime_checkable
class CatalogRepository(Protocol):
    """Port for reading authored catalog content by ``stable_id`` (Req 1.2, D3 §6)."""

    def get_module(self, stable_id: StableId) -> ModuleRecord | None:
        """Return the module with ``stable_id``, or ``None`` when absent.

        The returned :class:`ModuleRecord` carries the prerequisite concepts the
        unlocking service needs to compute the module's lock state (Req 2.1).
        """
        ...

    def list_modules_for_track(self, track_id: StableId) -> tuple[ModuleRecord, ...]:
        """Return every module of the track ``track_id``, ordered by ordinal.

        Ordering by :attr:`ModuleRecord.ordinal` gives progress and lock-state
        computation the modules in their authored sequence (D3 ``module.ordinal``).
        """
        ...

    def get_concept(self, stable_id: StableId) -> ConceptRecord | None:
        """Return the concept with ``stable_id`` and its prerequisite edges, or ``None``.

        The outgoing edges on the :class:`ConceptRecord` are the directly
        required concepts the prerequisite graph is built from (D3 §6, Req 2.1,
        13.4).
        """
        ...

    def get_quiz(self, stable_id: StableId) -> QuizRecord | None:
        """Return the quiz with ``stable_id`` and its questions/options, or ``None``.

        Scoring reads the returned :class:`QuizRecord` to judge answers and
        compute the percentage correct (Req 3.1, 3.5); review selection draws its
        eligible pool from the quiz's questions (Req 9.3).
        """
        ...

    def get_quiz_at_version(self, stable_id: StableId, content_version: str) -> QuizRecord | None:
        """Return the quiz as it was at ``content_version``, or ``None`` when unknown.

        Superseded revisions are retained, never hard-deleted, so an attempt that
        started under an older version can finish against it (Req 25.1, 26.3).
        """
        ...

    def list_questions_for_concept(self, concept_id: StableId) -> tuple[QuestionRecord, ...]:
        """Return every question covering the concept ``concept_id``.

        This is the *eligible pool* a spaced review draws from (Req 9.3, 28.1):
        the review service selects from it, and declines below the Config minimum.
        Ordered by ``stable_id`` so selection is deterministic.
        """
        ...

    def get_rubric_for_project(self, project_id: StableId) -> Rubric | None:
        """Return the rubric scoring the project ``project_id``, or ``None``.

        A project is scored by exactly one rubric (D3 ``project ||--|| rubric``);
        the rubric service weighs its criteria against the Config pass threshold
        once the ``ProjectReviewer`` completes (Req 10.1, 10.2, 10.3).
        """
        ...
