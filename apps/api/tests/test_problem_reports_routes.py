"""Route tests for ``POST /v1/problem-reports`` (ai-content task 20.2).

**Validates: Requirements 18.2, 18.3, 18.5, 18.6**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_domain import config
from edu_it_domain.ports.item_flags import ItemKey, ItemKind

_BODY = {
    "item_kind": "lesson",
    "item_id": "lesson-1",
    "content_version": "v3",
    "category": "unclear",
    "comment": "The second example is confusing.",
}


def _key(value: str = "k-1") -> dict[str, str]:
    return {"Idempotency-Key": value}


def test_report_is_stored_once_with_stable_id_and_content_version(served: Any) -> None:
    browser, _ = served.signed_in()

    first = browser.post("/v1/problem-reports", _BODY, headers=_key())
    again = browser.post("/v1/problem-reports", _BODY, headers=_key())

    assert first.status == 201
    assert again.status == 200
    assert again.json()["stable_id"] == first.json()["stable_id"]
    assert first.json()["replayed"] is False
    assert again.json()["replayed"] is True
    assert "comment" not in first.json()
    stored = served.adapters.problem_reports.get(first.json()["stable_id"])
    assert stored is not None
    assert stored.content_version == "v3"
    assert stored.comment == _BODY["comment"]
    reporters = served.adapters.problem_reports.reporters_for(
        ItemKey(ItemKind.LESSON, "lesson-1"), "v3"
    )
    assert reporters == frozenset({served.user_id(browser)})
    assert len(vars(served.adapters.problem_reports)["_reports"]) == 1


def test_reused_key_with_another_body_is_a_conflict(served: Any) -> None:
    browser, _ = served.signed_in()
    assert browser.post("/v1/problem-reports", _BODY, headers=_key()).status == 201

    other = browser.post("/v1/problem-reports", {**_BODY, "category": "other"}, headers=_key())

    assert other.status == 409
    assert other.is_problem


def test_idempotency_key_is_required(served: Any) -> None:
    browser, _ = served.signed_in()
    reply = browser.post("/v1/problem-reports", _BODY)
    assert reply.status == 422
    assert reply.is_problem


@pytest.mark.parametrize(
    ("locale", "title"), [("fr", "Signalement invalide"), ("en", "Invalid problem report")]
)
def test_oversize_comment_is_a_422_problem_and_nothing_is_stored(
    served: Any, locale: str, title: str
) -> None:
    browser, _ = served.signed_in()
    long = "x" * (config.PROBLEM_REPORT_COMMENT_MAX_CHARS + 1)

    reply = browser.post(
        "/v1/problem-reports", {**_BODY, "comment": long}, headers=_key(), query=f"locale={locale}"
    )

    assert reply.status == 422
    assert reply.is_problem
    assert reply.json()["title"] == title
    assert vars(served.adapters.problem_reports)["_reports"] == {}
    # The limit itself is accepted, and the failed call did not poison the key.
    ok = browser.post(
        "/v1/problem-reports",
        {**_BODY, "comment": "x" * config.PROBLEM_REPORT_COMMENT_MAX_CHARS},
        headers=_key(),
    )
    assert ok.status == 201


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_invalid_category_is_a_422_problem(served: Any, locale: str) -> None:
    browser, _ = served.signed_in()
    reply = browser.post(
        "/v1/problem-reports",
        {**_BODY, "category": "nonsense"},
        headers=_key(),
        query=f"locale={locale}",
    )
    assert reply.status == 422
    assert reply.is_problem
    assert reply.json()["type"].endswith("invalid-problem-report")
    assert vars(served.adapters.problem_reports)["_reports"] == {}


def test_report_requires_a_session_and_the_csrf_token(served: Any) -> None:
    browser, _ = served.signed_in()
    assert browser.post("/v1/problem-reports", _BODY, headers=_key(), csrf=False).status == 403
    anonymous = served.browser()
    assert anonymous.post("/v1/problem-reports", _BODY, headers=_key()).status in {401, 403}


def test_route_table_lists_the_route_behind_the_cookie_session() -> None:
    entry = next(e for e in ROUTE_TABLE if e.path == "/problem-reports")
    assert (entry.method, entry.authentication) == ("POST", "cookie")


def test_the_comment_has_no_path_to_an_llm_input() -> None:
    """The use case and its router import no LLM port, so the comment cannot reach a model."""
    root = Path(__file__).resolve().parents[3]
    for rel in (
        "packages/application/edu_it_application/ai/submit_problem_report.py",
        "apps/api/edu_it_api/problem_reports.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        imports = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
        assert not [ln for ln in imports if "llm" in ln.lower()], rel
