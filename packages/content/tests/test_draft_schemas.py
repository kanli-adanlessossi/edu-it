"""Tests for QuestionVariant and the AI stage draft schemas (ai-content task 2.1).

**Validates: Requirements 5.8, 5.10, 7.5**
"""

from __future__ import annotations

import pytest
from edu_it_content.schemas import (
    BriefDraft,
    LabDraft,
    LessonDraft,
    LessonSectionDraft,
    Localized,
    Origin,
    OutlineDraft,
    Provenance,
    QuestionOptionDraft,
    QuestionVariant,
    QuizDraft,
)
from pydantic import ValidationError


def _loc(text: str) -> dict[str, str]:
    return {"fr": f"{text} fr", "en": f"{text} en"}


def _opts() -> list[dict[str, object]]:
    return [
        {"text": _loc("a"), "is_correct": True},
        {"text": _loc("b"), "is_correct": False},
    ]


def test_lesson_draft_has_no_output_field() -> None:
    assert "output" not in LessonSectionDraft.model_fields
    with pytest.raises(ValidationError):
        LessonSectionDraft.model_validate(
            {"heading": _loc("h"), "body": _loc("b"), "commands": ["ls"], "output": "x"},
        )
    draft = LessonDraft.model_validate(
        {
            "title": _loc("t"),
            "sections": [{"heading": _loc("h"), "body": _loc("b"), "commands": ["ls"]}],
        },
    )
    assert draft.sections[0].commands == ("ls",)


def test_drafts_require_both_locales() -> None:
    with pytest.raises(ValidationError):
        BriefDraft.model_validate(
            {"title": {"en": "x"}, "description": _loc("d"), "learning_outcomes": [_loc("o")]},
        )


def test_drafts_carry_no_provenance_fields() -> None:
    for model in (BriefDraft, OutlineDraft, LessonDraft, QuizDraft, LabDraft):
        assert "provenance" not in model.model_fields


def test_quiz_draft_needs_a_correct_option() -> None:
    question = {
        "prompt_text": _loc("q"),
        "options": [{"text": _loc("a"), "is_correct": False}] * 2,
        "explanation": _loc("e"),
        "concept_title": _loc("c"),
    }
    with pytest.raises(ValidationError):
        QuizDraft.model_validate({"title": _loc("t"), "questions": [question]})


def test_lab_draft_requires_reference_solution() -> None:
    task = {
        "title": _loc("t"),
        "description": _loc("d"),
        "checks": [
            {
                "script": "true",
                "timeout_seconds": 5,
                "pass_message": _loc("ok"),
                "fail_message": _loc("ko"),
            },
        ],
    }
    base = {"title": _loc("l"), "kind": "terminal", "instructions": _loc("i"), "tasks": [task]}
    with pytest.raises(ValidationError):
        LabDraft.model_validate(base)
    assert LabDraft.model_validate({**base, "reference_solution": "touch x"}).reference_solution


def test_question_variant_carries_provenance_and_options() -> None:
    variant = QuestionVariant(
        provenance=Provenance(stable_id="v1", content_version="h", origin=Origin.AI, model="m"),
        parent_question_stable_id="q1",
        prompt_text=Localized(fr="q", en="q"),
        options=tuple(QuestionOptionDraft.model_validate(o) for o in _opts()),
        explanation=Localized(fr="e", en="e"),
    )
    assert variant.provenance.reviewed_by is None
    with pytest.raises(ValidationError):
        QuestionVariant(
            provenance=variant.provenance,
            parent_question_stable_id="q1",
            prompt_text=variant.prompt_text,
            options=variant.options[:1],
            explanation=variant.explanation,
        )
