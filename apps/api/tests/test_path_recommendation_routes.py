"""Route tests for ``/v1/path-recommendation`` (ai-content task 22.4).

**Validates: Requirements 17.7, 17.8**
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import ConceptRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.path_recommendations import (
    SOURCE_RULE_BASED,
    AdvisorAuditEntry,
    PathRecommendation,
)

_CONCEPT = "concept.advisor-a"
_AT = datetime(2025, 1, 1, tzinfo=UTC)


def _seed_concept(served: Any) -> None:
    catalog: Any = served.adapters.catalog
    catalog.add_concept(ConceptRecord(_CONCEPT))
    question = QuestionRecord(
        "question.advisor-a",
        options=(QuestionOption(option_id=1, is_correct=True),),
        concept_ids=(_CONCEPT,),
    )
    catalog.add_quiz(QuizRecord("quiz.advisor-a", "lesson.advisor-a", (question,), "v1"))


def _store(served: Any, user_id: str, stable_id: str = "rec-1") -> None:
    served.adapters.path_recommendations.record_run(
        PathRecommendation(stable_id, user_id, SOURCE_RULE_BASED, (_CONCEPT,), "run-1", _AT),
        AdvisorAuditEntry(f"audit-{stable_id}", user_id, "run-1", "h", SOURCE_RULE_BASED, "x", _AT),
    )


def test_no_recommendation_answers_204(served: Any) -> None:
    browser, _ = served.signed_in()

    reply = browser.get("/v1/path-recommendation")

    assert reply.status == 204
    assert reply.raw == b""


def test_recommendation_is_returned_then_hidden_after_dismissal(served: Any) -> None:
    _seed_concept(served)
    browser, _ = served.signed_in()
    _store(served, served.user_id(browser))

    shown = browser.get("/v1/path-recommendation")
    assert shown.status == 200
    assert shown.json()["stable_id"] == "rec-1"
    assert shown.json()["ordering"] == [_CONCEPT]

    first = browser.post("/v1/path-recommendation/rec-1/dismissal")
    again = browser.post("/v1/path-recommendation/rec-1/dismissal")
    assert first.status == again.status == 200
    assert first.json() == again.json()
    assert browser.get("/v1/path-recommendation").status == 204


def test_recommendation_with_no_eligible_item_left_answers_204(served: Any) -> None:
    browser, _ = served.signed_in()
    _store(served, served.user_id(browser))  # the concept was never servable

    assert browser.get("/v1/path-recommendation").status == 204


@pytest.mark.parametrize(
    ("locale", "title"),
    [("fr", "Recommandation introuvable"), ("en", "Recommendation not found")],
)
def test_dismissing_an_unknown_or_foreign_recommendation_is_a_translated_404(
    served: Any, locale: str, title: str
) -> None:
    owner, _ = served.signed_in()
    _store(served, served.user_id(owner))
    other, _ = served.signed_in()

    for target in ("rec-1", "missing"):
        reply = other.post(f"/v1/path-recommendation/{target}/dismissal", query=f"locale={locale}")
        assert reply.status == 404
        assert reply.is_problem
        assert reply.json()["title"] == title
        assert reply.json()["type"].endswith("recommendation-not-found")
    # The owner's recommendation was not touched by the other learner.
    stored = served.adapters.path_recommendations.get("rec-1")
    assert stored is not None
    assert stored.dismissed_at is None


def test_one_learner_never_sees_another_learners_recommendation(served: Any) -> None:
    _seed_concept(served)
    owner, _ = served.signed_in()
    _store(served, served.user_id(owner))
    other, _ = served.signed_in()

    assert other.get("/v1/path-recommendation").status == 204


def test_routes_need_a_session_and_dismissal_needs_the_csrf_token(served: Any) -> None:
    browser, _ = served.signed_in()
    assert browser.post("/v1/path-recommendation/rec-1/dismissal", csrf=False).status == 403

    anonymous = served.browser()
    assert anonymous.get("/v1/path-recommendation").status == 401
    assert anonymous.post("/v1/path-recommendation/rec-1/dismissal").status in {401, 403}


def test_route_table_lists_both_routes_behind_the_cookie_session() -> None:
    entries = {(e.method, e.path): e for e in ROUTE_TABLE}

    for key in (
        ("GET", "/path-recommendation"),
        ("POST", "/path-recommendation/{recommendation_id}/dismissal"),
    ):
        assert entries[key].authentication == "cookie"
