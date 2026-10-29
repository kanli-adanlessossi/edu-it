"""EDU-IT content schema layer (Req 12.1-12.4).

The single definition of every content format: one Pydantic v2 model per
content type plus the shared :class:`~edu_it_content.schemas.Provenance` record
and the reusable bilingual :class:`~edu_it_content.schemas.Localized` type that
requires both French and English (ADR A2). These schemas are the
authoring/validation contract the ``edu-it content validate``/``sync`` commands
consume and the single definition that validates AI output in a later phase.

This package depends on Pydantic v2 only — not a web framework — consistent with
the layering in ``.kiro/steering/structure.md`` and the learning-core design.
"""

from edu_it_content.schemas import (
    Concept,
    Lab,
    LabCheck,
    LabTask,
    Lesson,
    Localized,
    Module,
    Origin,
    Prerequisite,
    Project,
    Provenance,
    Question,
    QuestionOption,
    Quiz,
    Rubric,
    RubricCriterion,
    Track,
)

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
    "__version__",
]

__version__ = "0.0.0"
