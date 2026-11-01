"""Tests for the quiz and project read use cases (api-route-wiring task 6.1).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7**
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from edu_it_application.learning import GetProjectUseCase, GetQuizUseCase
from edu_it_application.learning.errors import NotFoundError
from edu_it_domain.learning.models import QuestionOption, Rubric, RubricCriterion
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.content_text import ProjectText, QuestionText, QuizText
from edu_it_infrastructure.fakes import In_Memory_CatalogRepository


class _Text:
    def __init__(self, quiz: QuizText | None, project: ProjectText | None) -> None:
        self._quiz = quiz
        self._project = project

    def quiz_text(self, quiz_id: str, locale: str) -> QuizText | None:
        return self._quiz if quiz_id == "qz_1" else None

    def project_text(self, project_id: str, locale: str) -> ProjectText | None:
        return self._project if project_id == "pj_1" else None


def _catalog() -> In_Memory_CatalogRepository:
    catalog = In_Memory_CatalogRepository()
    catalog.add_quiz(
        QuizRecord(
            stable_id="qz_1",
            lesson_id="ls_1",
            content_version="v1",
            questions=(
                QuestionRecord(
                    stable_id="qn_1",
                    options=(
                        QuestionOption(option_id=42, is_correct=True),
                        QuestionOption(option_id=7, is_correct=False),
                    ),
                ),
            ),
        )
    )
    catalog.add_rubric(
        Rubric(
            stable_id="rb_1",
            project_id="pj_1",
            criteria=(
                RubricCriterion(stable_id="rb_1#1", max_points=3),
                RubricCriterion(stable_id="rb_1#2", max_points=5),
            ),
        )
    )
    return catalog


_QUIZ_TEXT = QuizText(
    locale="en",
    title="Quiz",
    questions=(
        QuestionText(
            question_id="qn_1",
            prompt="Which?",
            option_texts={"1": "seven", "2": "forty-two"},
            hints=("h1",),
        ),
    ),
)
_PROJECT_TEXT = ProjectText(
    locale="en", title="Proj", brief="Brief", criterion_titles=("First", "Second")
)


def test_quiz_view_has_text_ranked_options_and_no_correctness() -> None:
    view = GetQuizUseCase(catalog=_catalog(), text=_Text(_QUIZ_TEXT, None)).execute("qz_1", "en")

    assert (view.title, view.content_version, view.locale) == ("Quiz", "v1", "en")
    question = view.questions[0]
    assert (question.prompt, question.hints) == ("Which?", ("h1",))
    assert [(o.option_id, o.text) for o in question.options] == [(7, "seven"), (42, "forty-two")]
    items: list[Any] = [view, question, *question.options]
    for item in items:
        assert not any("correct" in f.name for f in dataclasses.fields(item))


def test_project_view_pairs_criteria_with_titles() -> None:
    view = GetProjectUseCase(catalog=_catalog(), text=_Text(None, _PROJECT_TEXT)).execute(
        "pj_1", "en"
    )

    assert (view.title, view.description) == ("Proj", "Brief")
    assert [(c.stable_id, c.title, c.max_points) for c in view.rubric] == [
        ("rb_1#1", "First", 3),
        ("rb_1#2", "Second", 5),
    ]


def test_unknown_items_raise_not_found() -> None:
    text = _Text(_QUIZ_TEXT, _PROJECT_TEXT)
    with pytest.raises(NotFoundError):
        GetQuizUseCase(catalog=_catalog(), text=text).execute("nope", "en")
    with pytest.raises(NotFoundError):
        GetProjectUseCase(catalog=_catalog(), text=text).execute("nope", "en")
    # Known to the catalog but archived (no text): still not found.
    with pytest.raises(NotFoundError):
        GetQuizUseCase(catalog=_catalog(), text=_Text(None, None)).execute("qz_1", "en")
