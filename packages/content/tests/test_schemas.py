"""Schema tests for the content/provenance models (task 9.2, Req 12.2, 12.3).

These assert the authoring/validation contract defined in
:mod:`edu_it_content.schemas` enforces the invariants the content pipeline
relies on:

- A valid, fully-populated item of every provenanced content type parses
  successfully (Req 12.1, 12.2) — the happy path the ``edu-it content
  validate``/``sync`` commands depend on.
- A :class:`~edu_it_content.schemas.Localized` learner-facing value is rejected
  unless it carries **both** a French (``fr``) and an English (``en``) string,
  and neither may be empty (Req 12.3, ADR A2) — bilingual completeness is a
  type-level invariant.
- A provenanced item missing its required ``provenance`` record is rejected
  (Req 12.2) — provenance is mandatory on every content item.
- Any unknown/extra field is rejected (``extra="forbid"``) so a typo'd or stray
  key fails loudly rather than being silently dropped (Req 13.1).

Failure cases use ``pytest.raises(ValidationError)`` to assert the schema (not
some later check) is what rejects the malformed input.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from edu_it_content.schemas import (
    Concept,
    Lab,
    Lesson,
    Localized,
    Module,
    Origin,
    Project,
    Provenance,
    Question,
    Quiz,
    Track,
)
from pydantic import ValidationError

_NOW = datetime(2024, 1, 1, tzinfo=UTC)


def _localized(fr: str = "Titre", en: str = "Title") -> Localized:
    """A valid bilingual value, for reuse across the valid-item cases."""
    return Localized(fr=fr, en=en)


def _provenance(origin: Origin = Origin.HUMAN) -> Provenance:
    """A valid, fully-populated provenance record, for reuse across cases.

    Populates the optional AI-generation and review trail fields too so the
    valid-item cases exercise a fully-populated provenance, not just the
    always-required anchors.
    """
    return Provenance(
        stable_id="st_1",
        content_version="cv_1",
        origin=origin,
        model="test-model",
        prompt_id="pr_1",
        prompt_version="v1",
        generation_date=_NOW,
        reviewed_by="usr_reviewer",
        reviewed_at=_NOW,
    )


# --- Valid, fully-populated items parse (Req 12.1, 12.2) ---------------------


def test_valid_track_parses() -> None:
    track = Track(provenance=_provenance(), title=_localized(), status="published")
    assert track.title.fr == "Titre"
    assert track.provenance.origin is Origin.HUMAN


def test_valid_module_parses() -> None:
    module = Module(
        provenance=_provenance(),
        track_stable_id="tr_1",
        ordinal=0,
        title=_localized(),
    )
    assert module.ordinal == 0
    assert module.track_stable_id == "tr_1"


def test_valid_lesson_parses() -> None:
    lesson = Lesson(
        provenance=_provenance(),
        module_stable_id="md_1",
        ordinal=1,
        title=_localized(),
    )
    assert lesson.module_stable_id == "md_1"


def test_valid_lab_parses() -> None:
    lab = Lab(provenance=_provenance(), lesson_stable_id="ls_1", title=_localized())
    assert lab.lesson_stable_id == "ls_1"


def test_valid_project_parses() -> None:
    project = Project(
        provenance=_provenance(),
        title=_localized("Projet", "Project"),
        description=_localized("Desc fr", "Desc en"),
    )
    assert project.description.en == "Desc en"


def test_valid_quiz_parses() -> None:
    quiz = Quiz(provenance=_provenance(), lesson_stable_id="ls_1", title=_localized())
    assert quiz.lesson_stable_id == "ls_1"


def test_valid_question_parses() -> None:
    question = Question(
        provenance=_provenance(),
        quiz_stable_id="qz_1",
        prompt_text=_localized("Question fr", "Question en"),
    )
    assert question.quiz_stable_id == "qz_1"


def test_valid_concept_parses() -> None:
    concept = Concept(provenance=_provenance(origin=Origin.AI), title=_localized())
    assert concept.provenance.origin is Origin.AI


def test_valid_provenance_with_only_required_fields_parses() -> None:
    # The AI-generation and review trail fields are optional; a provenance with
    # only the always-required anchors is valid (Req 12.2).
    provenance = Provenance(stable_id="st_1", content_version="cv_1", origin=Origin.HUMAN)
    assert provenance.model is None
    assert provenance.reviewed_by is None


# --- Missing-locale rejection (Req 12.3, ADR A2) -----------------------------


def test_localized_missing_fr_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Localized(en="Title")  # type: ignore[call-arg]


def test_localized_missing_en_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Localized(fr="Titre")  # type: ignore[call-arg]


def test_localized_empty_fr_is_rejected() -> None:
    # An empty string is not a translation; min_length=1 rejects it (Req 12.3).
    with pytest.raises(ValidationError):
        Localized(fr="", en="Title")


def test_localized_empty_en_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Localized(fr="Titre", en="")


def test_item_with_locale_missing_a_language_is_rejected() -> None:
    # Bilingual completeness is enforced wherever a Localized field appears, not
    # only on a bare Localized: a Track whose title omits `en` is rejected.
    with pytest.raises(ValidationError):
        Track(provenance=_provenance(), title={"fr": "Titre"}, status="published")  # type: ignore[arg-type]


# --- Missing-provenance rejection (Req 12.2) ---------------------------------


def test_track_missing_provenance_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Track(title=_localized(), status="published")  # type: ignore[call-arg]


def test_concept_missing_provenance_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Concept(title=_localized())  # type: ignore[call-arg]


def test_provenance_missing_origin_is_rejected() -> None:
    # Origin is one of the always-required anchors on provenance (Req 12.2).
    with pytest.raises(ValidationError):
        Provenance(stable_id="st_1", content_version="cv_1")  # type: ignore[call-arg]


# --- Unknown/extra field rejection (extra="forbid", Req 13.1) ----------------


def test_track_with_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Track(
            provenance=_provenance(),
            title=_localized(),
            status="published",
            unexpected="x",  # type: ignore[call-arg]
        )


def test_provenance_with_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            stable_id="st_1",
            content_version="cv_1",
            origin=Origin.HUMAN,
            typo_field="x",  # type: ignore[call-arg]
        )


def test_localized_with_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Localized(fr="Titre", en="Title", de="Titel")  # type: ignore[call-arg]
