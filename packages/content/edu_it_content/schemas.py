"""Pydantic v2 content schemas and shared types (Req 12.1-12.4).

This module is the single definition of every EDU-IT content format. It defines
one Pydantic v2 model per content type — ``track``, ``module``, ``lesson``,
``lab``, ``lab_task``, ``lab_check``, ``project``, ``rubric``,
``rubric_criterion``, ``quiz``, ``question``, ``question_option``, ``concept``,
and ``prerequisite`` (Req 12.1, ``docs/DATA-MODEL.md`` D3 §6) — plus the shared
building blocks every content item is built from:

- :class:`Localized` — the reusable bilingual type that requires **both** a
  French (``fr``) and an English (``en``) value for every learner-facing field
  (Req 12.3, ADR A2). Input missing either locale is rejected.
- :class:`Provenance` — the shared provenance record carried by every content
  item: ``origin``, ``model``, ``prompt_id``, ``prompt_version``,
  ``generation_date``, ``reviewed_by``, ``reviewed_at``, plus a
  ``content_version`` hash and a ``stable_id`` (Req 12.2, D3 §5).

These schemas are the authoring/validation contract the ``edu-it content
validate`` and ``edu-it content sync`` commands consume, and the single
definition that will validate AI output in a later phase (Req 12.4). The field
set of each model mirrors its D3 catalog table (``docs/DATA-MODEL.md`` D3 §6),
with learner-facing text expressed as :class:`Localized` so bilingual
completeness is enforced at the type level rather than by a separate check.

Models are ``frozen`` and reject unknown fields (``extra="forbid"``) so an
authored item with a typo'd or stray key fails validation loudly rather than
silently dropping data — the content pipeline must reject malformed content
(Req 13.1).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Concept",
    "Lab",
    "LabCheck",
    "LabTask",
    "Lesson",
    "Localized",
    "Module",
    "Origin",
    "Prerequisite",
    "Project",
    "Provenance",
    "Question",
    "QuestionOption",
    "Quiz",
    "Rubric",
    "RubricCriterion",
    "Track",
]

#: The shared model configuration for every content schema: immutable
#: (``frozen``) so a validated item cannot be mutated after construction, and
#: ``extra="forbid"`` so an unknown or misspelled key is rejected rather than
#: silently ignored (Req 13.1).
_CONTENT_MODEL_CONFIG: Final[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class _ContentModel(BaseModel):
    """Base for every content schema, applying the shared model configuration.

    Centralizing :data:`_CONTENT_MODEL_CONFIG` here keeps all content models
    frozen and strict about unknown fields without repeating the config on each
    subclass.
    """

    model_config = _CONTENT_MODEL_CONFIG


class Origin(StrEnum):
    """How a content item was authored (Req 12.2, D3 §5).

    ``AI`` marks an item drafted by the AI generation pipeline; ``HUMAN`` marks
    an item authored directly by a person. An ``AI`` item requires a recorded
    reviewer before it may be served — that rule is enforced by the validate
    command (Req 13.6), not by this schema, since provenance is captured at
    authoring time and the reviewer is attached on approval.
    """

    AI = "ai"
    HUMAN = "human"


class Localized(_ContentModel):
    """A learner-facing value in both supported locales (Req 12.3, ADR A2).

    Every learner-facing field on a content item is a :class:`Localized` value
    requiring **both** a French (:attr:`fr`) and an English (:attr:`en`) string.
    Because both fields are required with no default, input that omits either
    locale is rejected with a Pydantic validation error — bilingual
    completeness is a type-level invariant, not a separate check.
    """

    fr: str = Field(min_length=1, description="The French (fr) value; required.")
    en: str = Field(min_length=1, description="The English (en) value; required.")


class Provenance(_ContentModel):
    """The shared provenance record carried by every content item (Req 12.2, D3 §5).

    Provenance is required on every content item. It anchors the item's immutable
    identity (:attr:`stable_id`) and the hash of its current content
    (:attr:`content_version`) — the pair that drives idempotent content sync
    (D3 §1.2, §10) — alongside the AI generation trail (:attr:`origin`,
    :attr:`model`, :attr:`prompt_id`, :attr:`prompt_version`,
    :attr:`generation_date`) and the expert-approval trail (:attr:`reviewed_by`,
    :attr:`reviewed_at`).

    The generation-trail and review-trail fields are optional because a
    human-authored item has no AI generation trail and an item carries no
    reviewer until it is approved; :attr:`origin`, :attr:`stable_id`, and
    :attr:`content_version` are always required. The rule that an ``ai`` item
    must have a recorded reviewer before serving is enforced by the validate
    command (Req 13.6).
    """

    stable_id: str = Field(
        min_length=1,
        description="Immutable identity anchor for the item (D3 §1.2); required.",
    )
    content_version: str = Field(
        min_length=1,
        description="Hash that changes whenever the item's content changes (D3 §1.2).",
    )
    origin: Origin = Field(description="Whether the item is AI-drafted or human-authored.")
    model: str | None = Field(
        default=None,
        description="The model that generated the item, for AI-drafted content.",
    )
    prompt_id: str | None = Field(
        default=None,
        description="Identifier of the prompt that generated the item.",
    )
    prompt_version: str | None = Field(
        default=None,
        description="Version of the prompt that generated the item.",
    )
    generation_date: datetime | None = Field(
        default=None,
        description="When the item was generated, for AI-drafted content.",
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Stable id of the user who approved the item, once reviewed.",
    )
    reviewed_at: datetime | None = Field(
        default=None,
        description="When the item was approved, once reviewed.",
    )


class _ProvenancedContent(_ContentModel):
    """Base for content items that carry their own provenance record (Req 12.2).

    Catalog items that reference ``content_provenance`` one-to-one in D3 §6
    (``track``, ``module``, ``lesson``, ``lab``, ``project``, ``quiz``,
    ``question``, ``concept``) carry a required :attr:`provenance`. Sub-items
    that D3 models without their own provenance row (``lab_task``,
    ``lab_check``, ``rubric``, ``rubric_criterion``, ``question_option``,
    ``prerequisite``) do not inherit from this base.
    """

    provenance: Provenance = Field(description="Required provenance for this content item.")


class Track(_ProvenancedContent):
    """A top-level learning track (D3 §6, Req 12.1).

    Carries a bilingual :attr:`title` and a :attr:`status`; its modules are
    authored as separate :class:`Module` items referencing the track by
    ``stable_id`` and resolved by the validate command (Req 13.3).
    """

    title: Localized = Field(description="The track's bilingual title.")
    status: str = Field(min_length=1, description="The track's lifecycle status.")


class Module(_ProvenancedContent):
    """An ordered unit within a track (D3 §6, Req 12.1).

    :attr:`track_stable_id` references the owning :class:`Track`; :attr:`ordinal`
    is the module's position within the track. :attr:`title` is bilingual.
    """

    track_stable_id: str = Field(min_length=1, description="Stable id of the owning track.")
    ordinal: int = Field(ge=0, description="Position of the module within its track.")
    title: Localized = Field(description="The module's bilingual title.")


class Lesson(_ProvenancedContent):
    """An ordered unit within a module (D3 §6, Req 12.1).

    :attr:`module_stable_id` references the owning :class:`Module`;
    :attr:`ordinal` is the lesson's position within the module. :attr:`title` is
    bilingual; the MDX body is authored separately and rendered through the
    allowlist (ADR A5), so it is not part of this schema.
    """

    module_stable_id: str = Field(min_length=1, description="Stable id of the owning module.")
    ordinal: int = Field(ge=0, description="Position of the lesson within its module.")
    title: Localized = Field(description="The lesson's bilingual title.")


class Lab(_ProvenancedContent):
    """A hands-on lab attached to a lesson (D3 §6, Req 12.1).

    :attr:`lesson_stable_id` references the owning :class:`Lesson`. Labs are
    surfaced as "coming soon" in this phase (Req 15.7), but their schema is
    defined so authored content can carry them.
    """

    lesson_stable_id: str = Field(min_length=1, description="Stable id of the owning lesson.")
    title: Localized = Field(description="The lab's bilingual title.")


class LabTask(_ContentModel):
    """An ordered step within a lab (D3 §6, Req 12.1).

    :attr:`lab_stable_id` references the owning :class:`Lab`; :attr:`ordinal` is
    the task's position within the lab. D3 models ``lab_task`` without its own
    provenance row, so it carries none.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the task.")
    lab_stable_id: str = Field(min_length=1, description="Stable id of the owning lab.")
    ordinal: int = Field(ge=0, description="Position of the task within its lab.")
    title: Localized = Field(description="The lab task's bilingual title.")


class LabCheck(_ContentModel):
    """A graded check on a lab task (D3 §6, Req 12.1).

    :attr:`lab_task_stable_id` references the owning :class:`LabTask`;
    :attr:`check_kind` identifies how the check is graded. A passing lab check
    contributes to reaching ``Mastered`` where a lab covers the concept
    (domain rules), but that is a domain behavior, not a schema concern.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the check.")
    lab_task_stable_id: str = Field(min_length=1, description="Stable id of the owning lab task.")
    check_kind: str = Field(min_length=1, description="How the check is graded.")


class RubricCriterion(_ContentModel):
    """A weighted criterion of a rubric (D3 §6, Req 12.1).

    :attr:`rubric_stable_id` references the owning :class:`Rubric`;
    :attr:`max_points` is the criterion's weight. :attr:`title` is bilingual so
    the criterion is presented in both locales. D3 models ``rubric_criterion``
    without its own provenance row.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the criterion.")
    rubric_stable_id: str = Field(min_length=1, description="Stable id of the owning rubric.")
    max_points: int = Field(gt=0, description="The criterion's weight in rubric points.")
    title: Localized = Field(description="The criterion's bilingual title.")


class Rubric(_ContentModel):
    """A project scoring rubric (D3 §6, Req 12.1).

    :attr:`project_stable_id` references the :class:`Project` it scores; a
    project is scored by exactly one rubric. The rubric's criteria are authored
    as separate :class:`RubricCriterion` items referencing the rubric by
    ``stable_id``. D3 models ``rubric`` without its own provenance row.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the rubric.")
    project_stable_id: str = Field(min_length=1, description="Stable id of the scored project.")


class Project(_ProvenancedContent):
    """A project scored by exactly one rubric (D3 §6, Req 12.1).

    :attr:`title` and :attr:`description` are bilingual so the project's brief is
    presented in both locales. The scoring :class:`Rubric` references the project
    by ``stable_id``.
    """

    title: Localized = Field(description="The project's bilingual title.")
    description: Localized = Field(description="The project's bilingual description/brief.")


class QuestionOption(_ContentModel):
    """An answer option for a question (D3 §6, Req 12.1).

    :attr:`question_stable_id` references the owning :class:`Question`;
    :attr:`is_correct` marks whether selecting this option is correct (D3 §6,
    §9). :attr:`text` is bilingual. D3 models ``question_option`` without its own
    provenance row.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the option.")
    question_stable_id: str = Field(min_length=1, description="Stable id of the owning question.")
    is_correct: bool = Field(description="Whether selecting this option is correct.")
    text: Localized = Field(description="The option's bilingual text.")


class Question(_ProvenancedContent):
    """A quiz question (D3 §6, Req 12.1).

    :attr:`quiz_stable_id` references the owning :class:`Quiz`;
    :attr:`prompt_text` is the bilingual question prompt. Options are authored as
    separate :class:`QuestionOption` items referencing the question by
    ``stable_id``.
    """

    quiz_stable_id: str = Field(min_length=1, description="Stable id of the owning quiz.")
    prompt_text: Localized = Field(description="The question's bilingual prompt text.")


class Quiz(_ProvenancedContent):
    """A quiz attached to a lesson (D3 §6, Req 12.1).

    :attr:`lesson_stable_id` references the owning :class:`Lesson`;
    :attr:`title` is bilingual. The quiz's questions are authored as separate
    :class:`Question` items referencing the quiz by ``stable_id``.
    """

    lesson_stable_id: str = Field(min_length=1, description="Stable id of the owning lesson.")
    title: Localized = Field(description="The quiz's bilingual title.")


class Concept(_ProvenancedContent):
    """A unit of knowledge mastery is measured against (D3 §6, §7, Req 12.1).

    Concepts carry a bilingual :attr:`title`. Prerequisite relationships between
    concepts are authored as separate :class:`Prerequisite` edges; mastery and
    review state are learner data, not content, so they are not part of this
    schema.
    """

    title: Localized = Field(description="The concept's bilingual title.")


class Prerequisite(_ContentModel):
    """A directed prerequisite edge between two concepts (D3 §6, Req 12.1).

    The edge expresses that :attr:`concept_stable_id` requires
    :attr:`requires_concept_stable_id` to be mastered first. The validate command
    checks the overall prerequisite graph for cycles (Req 13.4). D3 models
    ``prerequisite`` without its own provenance row.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the edge.")
    concept_stable_id: str = Field(
        min_length=1,
        description="Stable id of the dependent concept.",
    )
    requires_concept_stable_id: str = Field(
        min_length=1,
        description="Stable id of the concept that must be mastered first.",
    )
