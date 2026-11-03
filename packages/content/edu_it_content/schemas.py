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

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BriefDraft",
    "Concept",
    "Lab",
    "LabCheck",
    "LabCheckDraft",
    "LabCheckOutcome",
    "LabDraft",
    "LabKind",
    "LabTask",
    "LabTaskDraft",
    "Lesson",
    "LessonDraft",
    "LessonOutlineDraft",
    "LessonSectionDraft",
    "Localized",
    "Module",
    "ModuleOutlineDraft",
    "Origin",
    "OutlineDraft",
    "Prerequisite",
    "Project",
    "ProjectDraft",
    "Provenance",
    "Question",
    "QuestionDraft",
    "QuestionOption",
    "QuestionOptionDraft",
    "QuestionVariant",
    "Quiz",
    "QuizDraft",
    "Rubric",
    "RubricCriterion",
    "RubricCriterionDraft",
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


class LabKind(StrEnum):
    """The kind of a lab (hands-on-labs design, content schema table).

    ``TERMINAL`` labs attach an xterm.js terminal to the Sandbox; ``CODE`` labs
    are Code_Exercises edited in Monaco and tested inside the Sandbox.
    """

    TERMINAL = "terminal"
    CODE = "code"


#: The mandatory outcome code every :class:`LabCheck` must declare (Req 20.1).
PASS_OUTCOME_CODE: Final[str] = "pass"

#: The default directory the Check_Bundle is copied into at grading time.
DEFAULT_CHECK_BUNDLE_DEST: Final[str] = "/grader"


class Lab(_ProvenancedContent):
    """A hands-on lab attached to a lesson (D3 §6, Req 12.1, 20.1).

    :attr:`lesson_stable_id` references the owning :class:`Lesson`.
    :attr:`kind` is ``terminal`` or ``code``; :attr:`image` names the Lab_Image;
    :attr:`concept_stable_ids` lists the concepts the lab covers.
    :attr:`check_bundle` is the repository path of the Check_Bundle, copied into
    the Sandbox at :attr:`check_bundle_dest` only at grading time.
    :attr:`learner_files` names the flat learner files graded for a code lab and
    :attr:`starter_files` the repository paths of the starter skeletons.
    Reference and location rules are enforced by the validate command
    (Req 20.3, 20.6), not by this schema.
    """

    lesson_stable_id: str = Field(min_length=1, description="Stable id of the owning lesson.")
    title: Localized = Field(description="The lab's bilingual title.")
    kind: LabKind = Field(description="Whether the lab is a terminal lab or a code exercise.")
    image: str = Field(min_length=1, description="Name of the Lab_Image the Sandbox runs.")
    concept_stable_ids: tuple[str, ...] = Field(
        default=(),
        description="Stable ids of the concepts the lab covers.",
    )
    instructions: Localized = Field(description="The lab's bilingual instructions.")
    setup_script: str | None = Field(
        default=None,
        description="Repository path of the script that prepares the Sandbox, if any.",
    )
    check_bundle: str | None = Field(
        default=None,
        description="Repository path of the read-only Check_Bundle directory.",
    )
    check_bundle_dest: str = Field(
        default=DEFAULT_CHECK_BUNDLE_DEST,
        min_length=1,
        description="Sandbox directory the Check_Bundle is copied into at grading time.",
    )
    learner_files: tuple[str, ...] = Field(
        default=(),
        description="Flat names of the learner files a code lab grades.",
    )
    starter_files: tuple[str, ...] = Field(
        default=(),
        description="Repository paths of the starter files provided to the learner.",
    )


class LabTask(_ContentModel):
    """An ordered step within a lab (D3 §6, Req 12.1, 20.1).

    :attr:`lab_stable_id` references the owning :class:`Lab`; :attr:`ordinal` is
    the task's position within the lab. :attr:`required` marks whether the task
    must be completed for the lab to count as passed; :attr:`hints` are the
    progressive bilingual hints, in reveal order. D3 models ``lab_task`` without
    its own provenance row, so it carries none.
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the task.")
    lab_stable_id: str = Field(min_length=1, description="Stable id of the owning lab.")
    ordinal: int = Field(ge=0, description="Position of the task within its lab.")
    title: Localized = Field(description="The lab task's bilingual title.")
    description: Localized = Field(description="The lab task's bilingual description.")
    required: bool = Field(default=True, description="Whether the task is required for the lab.")
    hints: tuple[Localized, ...] = Field(
        default=(),
        description="Progressive bilingual hints, in reveal order.",
    )


class LabCheckOutcome(_ContentModel):
    """One possible result of a :class:`LabCheck` (hands-on-labs design).

    :attr:`code` is the symbolic outcome name (``pass``, ``wrong-output``, ...);
    :attr:`exit_code` is the script exit status mapped to it, or ``None`` when
    the outcome is reported through the result channel instead.
    :attr:`feedback_key` is the stable identifier of the learner-facing
    :attr:`message`, which must carry both translations and never include
    expected output.
    """

    code: str = Field(min_length=1, description="Symbolic outcome code.")
    exit_code: int | None = Field(
        default=None,
        ge=0,
        le=255,
        description="Check script exit status mapped to this outcome, if any.",
    )
    feedback_key: str = Field(min_length=1, description="Stable Feedback_Key of the message.")
    message: Localized = Field(description="The outcome's bilingual learner-facing message.")


class LabCheck(_ContentModel):
    """A graded check on a lab task (D3 §6, Req 12.1, 20.1).

    :attr:`lab_task_stable_id` references the owning :class:`LabTask`.
    :attr:`timeout_seconds` is the per-Check timeout and :attr:`script` the
    Check_Bundle script that is run. :attr:`outcomes` lists the possible
    results; one with code ``pass`` is mandatory and codes must be unique. The
    bound on :attr:`timeout_seconds` (Usable_Run_Budget) and the Feedback_Key
    completeness rules are enforced by the validate command (Req 20.4, 20.5).
    """

    stable_id: str = Field(min_length=1, description="Immutable identity anchor for the check.")
    lab_task_stable_id: str = Field(min_length=1, description="Stable id of the owning lab task.")
    timeout_seconds: int = Field(gt=0, description="Per-Check timeout in seconds.")
    script: str = Field(min_length=1, description="Check_Bundle path of the check script.")
    outcomes: tuple[LabCheckOutcome, ...] = Field(
        description="Possible outcomes; a `pass` outcome is mandatory.",
    )

    @model_validator(mode="after")
    def _outcomes_have_unique_codes_and_pass(self) -> LabCheck:
        codes = [outcome.code for outcome in self.outcomes]
        if PASS_OUTCOME_CODE not in codes:
            msg = f"outcomes must include the mandatory {PASS_OUTCOME_CODE!r} code"
            raise ValueError(msg)
        if len(set(codes)) != len(codes):
            msg = "outcome codes must be unique"
            raise ValueError(msg)
        return self


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


# ---------------------------------------------------------------------------
# AI generation: Question_Variant and the per-Stage draft schemas
# (ai-content Req 5.8, 5.10, 7.5)
# ---------------------------------------------------------------------------
#
# The ``*Draft`` models are the *output schemas* the LLM is asked to fill, one
# per Stage (brief, outline, lessons, quizzes, labs, project). They hold
# learner-facing text as :class:`Localized` (both FR and EN required) and
# nothing else the model could invent about identity or trust: ``stable_id``,
# ``content_version`` and every :class:`Provenance` field are attached by the
# Generator after validation (Req 5.10), never produced by the model, and a
# draft carries no reviewer. Drafts are consumed by the Generator, which
# assembles the final content items from them; they are not synced content.


class BriefDraft(_ContentModel):
    """The brief Stage output: what a track is about (ai-content Req 5.8)."""

    title: Localized = Field(description="The track's bilingual title.")
    description: Localized = Field(description="Bilingual summary of the track.")
    learning_outcomes: tuple[Localized, ...] = Field(
        min_length=1,
        description="Bilingual learning outcomes of the track.",
    )


class LessonOutlineDraft(_ContentModel):
    """One lesson entry of an outline (ai-content Req 5.8)."""

    title: Localized = Field(description="The lesson's bilingual title.")
    summary: Localized = Field(description="Bilingual summary of what the lesson teaches.")
    concept_titles: tuple[Localized, ...] = Field(
        default=(),
        description="Bilingual titles of the concepts the lesson covers.",
    )


class ModuleOutlineDraft(_ContentModel):
    """One module entry of an outline, with its ordered lessons."""

    title: Localized = Field(description="The module's bilingual title.")
    lessons: tuple[LessonOutlineDraft, ...] = Field(
        min_length=1,
        description="The module's lessons, in order.",
    )


class OutlineDraft(_ContentModel):
    """The outline Stage output: ordered modules and their lessons."""

    modules: tuple[ModuleOutlineDraft, ...] = Field(
        min_length=1,
        description="The track's modules, in order.",
    )


class LessonSectionDraft(_ContentModel):
    """A section of a drafted lesson: bilingual prose plus shell commands.

    :attr:`commands` holds the commands only. There is deliberately **no output
    field**: command output is never produced by the model. The Verifier runs
    the commands in a Verification_Sandbox and the Generator assembles the MDX
    from the draft plus those real outputs (ai-content Req 7.5).
    """

    heading: Localized = Field(description="The section's bilingual heading.")
    body: Localized = Field(description="The section's bilingual prose.")
    commands: tuple[str, ...] = Field(
        default=(),
        description="Shell commands shown in the section; their outputs are not drafted.",
    )


class LessonDraft(_ContentModel):
    """The lessons Stage output for one lesson (commands only, no output field)."""

    title: Localized = Field(description="The lesson's bilingual title.")
    sections: tuple[LessonSectionDraft, ...] = Field(
        min_length=1,
        description="The lesson's sections, in order.",
    )


class QuestionOptionDraft(_ContentModel):
    """An answer option of a drafted question or variant."""

    text: Localized = Field(description="The option's bilingual text.")
    is_correct: bool = Field(description="Whether selecting this option is correct.")


def _check_options(options: tuple[QuestionOptionDraft, ...]) -> None:
    """Require at least two options of which at least one is correct."""
    if len(options) < 2:
        msg = "a question needs at least two options"
        raise ValueError(msg)
    if not any(option.is_correct for option in options):
        msg = "a question needs at least one correct option"
        raise ValueError(msg)


class QuestionDraft(_ContentModel):
    """A drafted quiz question with its answer key."""

    prompt_text: Localized = Field(description="The question's bilingual prompt text.")
    options: tuple[QuestionOptionDraft, ...] = Field(description="The answer options.")
    explanation: Localized = Field(description="Bilingual explanation of the correct answer.")
    concept_title: Localized = Field(description="Bilingual title of the concept it checks.")

    @model_validator(mode="after")
    def _options_are_answerable(self) -> QuestionDraft:
        _check_options(self.options)
        return self


class QuizDraft(_ContentModel):
    """The quizzes Stage output for one lesson."""

    title: Localized = Field(description="The quiz's bilingual title.")
    questions: tuple[QuestionDraft, ...] = Field(
        min_length=1,
        description="The quiz's questions.",
    )


class LabCheckDraft(_ContentModel):
    """A drafted lab check: a script that must fail at the start and pass on the solution."""

    script: str = Field(min_length=1, description="Source of the check script.")
    timeout_seconds: int = Field(gt=0, description="Per-check timeout in seconds.")
    pass_message: Localized = Field(description="Bilingual message shown when the check passes.")
    fail_message: Localized = Field(description="Bilingual message shown when the check fails.")


class LabTaskDraft(_ContentModel):
    """A drafted lab task with its checks and progressive hints."""

    title: Localized = Field(description="The task's bilingual title.")
    description: Localized = Field(description="The task's bilingual description.")
    hints: tuple[Localized, ...] = Field(default=(), description="Progressive bilingual hints.")
    checks: tuple[LabCheckDraft, ...] = Field(
        min_length=1,
        description="The checks grading this task.",
    )


class LabDraft(_ContentModel):
    """The labs Stage output for one lab.

    :attr:`reference_solution` is the script that solves the lab; the Verifier
    applies it in a Verification_Sandbox and every check must pass there while
    failing on the Starting_State (ai-content Req 8.1 to 8.4).
    """

    title: Localized = Field(description="The lab's bilingual title.")
    kind: LabKind = Field(description="Whether the lab is a terminal lab or a code exercise.")
    instructions: Localized = Field(description="The lab's bilingual instructions.")
    setup_script: str | None = Field(
        default=None,
        description="Script that builds the Starting_State, if any.",
    )
    tasks: tuple[LabTaskDraft, ...] = Field(min_length=1, description="The lab's tasks.")
    reference_solution: str = Field(
        min_length=1,
        description="Script that solves the lab from the Starting_State.",
    )


class RubricCriterionDraft(_ContentModel):
    """A drafted weighted rubric criterion."""

    title: Localized = Field(description="The criterion's bilingual title.")
    max_points: int = Field(gt=0, description="The criterion's weight in rubric points.")


class ProjectDraft(_ContentModel):
    """The project Stage output: the project brief and its rubric criteria."""

    title: Localized = Field(description="The project's bilingual title.")
    description: Localized = Field(description="The project's bilingual description/brief.")
    criteria: tuple[RubricCriterionDraft, ...] = Field(
        min_length=1,
        description="The rubric criteria scoring the project.",
    )


class QuestionVariant(_ProvenancedContent):
    """A generated alternative of an approved Parent_Question (ai-content Req 13, D6).

    Unlike a draft, a variant is a stored item and carries the shared
    :class:`Provenance` (``origin``, ``model``, ``prompt_id``,
    ``prompt_version``, ``generation_date``, and the reviewer once approved).
    :attr:`parent_question_stable_id` references the :class:`Question` it
    varies; a served variant is presented under that parent's ``stable_id``.
    Its lifecycle state lives in the ``variant`` table, not in this schema.
    """

    parent_question_stable_id: str = Field(
        min_length=1,
        description="Stable id of the approved Parent_Question this variant derives from.",
    )
    prompt_text: Localized = Field(description="The variant's bilingual prompt text.")
    options: tuple[QuestionOptionDraft, ...] = Field(description="The answer options.")
    explanation: Localized = Field(description="Bilingual explanation of the correct answer.")

    @model_validator(mode="after")
    def _options_are_answerable(self) -> QuestionVariant:
        _check_options(self.options)
        return self
