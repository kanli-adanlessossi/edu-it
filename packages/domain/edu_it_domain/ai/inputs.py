"""Typed LLM input kinds and their closed field allowlists (ai-content Req 3.1, 7.2).

This is the structural half of D6 Section 6: personal data and terminal I/O
have no field to go in. ``VERIFY_ANSWER`` is the blind-verification kind; it
has no field that could hold the answer key (Req 7.2).
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from edu_it_domain.ports.llm_provider import LLMInput

__all__ = ["INPUT_FIELDS", "InputKind", "disallowed_fields", "input_kind"]


class InputKind(StrEnum):
    """Every kind of request the pipeline may send to a model."""

    BRIEF = "brief"
    OUTLINE = "outline"
    LESSON = "lesson"
    QUIZ = "quiz"
    LAB = "lab"
    PROJECT = "project"
    VARIANT = "variant"
    VERIFY_ANSWER = "verify_answer"
    ADVISOR = "advisor"
    EVAL = "eval"


#: Closed allowlist of top-level field names per kind.
INPUT_FIELDS: Final = MappingProxyType(
    {
        InputKind.BRIEF: frozenset({"track", "brief", "locale", "glossary"}),
        InputKind.OUTLINE: frozenset({"track", "brief", "locale", "glossary", "concepts"}),
        InputKind.LESSON: frozenset(
            {"track", "module", "concept", "outline", "locale", "glossary", "sources"}
        ),
        InputKind.QUIZ: frozenset(
            {"track", "module", "concept", "lesson", "locale", "glossary", "difficulty"}
        ),
        InputKind.LAB: frozenset(
            {"track", "module", "concept", "lesson", "locale", "glossary", "image", "kind"}
        ),
        InputKind.PROJECT: frozenset(
            {"track", "module", "concepts", "outline", "locale", "glossary", "rubric"}
        ),
        InputKind.VARIANT: frozenset(
            {"parent_question", "parent_options", "concept", "locale", "glossary", "count"}
        ),
        # No key, answer or explanation field: the verifier answers blind.
        InputKind.VERIFY_ANSWER: frozenset({"question", "options", "locale"}),
        InputKind.ADVISOR: frozenset(
            {
                "handle",
                "concept_mastery",
                "completion",
                "review_state",
                "prerequisites",
                "eligible_ids",
                "limit",
            }
        ),
        InputKind.EVAL: frozenset({"case_id", "prompt_input", "locale"}),
    }
)


def input_kind(value: LLMInput) -> InputKind:
    """Return the ``InputKind`` of ``value``; raise ``ValueError`` if unknown."""
    return InputKind(value.kind)


def disallowed_fields(value: LLMInput) -> tuple[str, ...]:
    """Return the sorted field names of ``value`` outside its kind's allowlist."""
    allowed = INPUT_FIELDS[input_kind(value)]
    return tuple(sorted(name for name in value.fields if name not in allowed))
