"""API tests for the reviewer queue problem documents, route table and OpenAPI (ai-content 13.5).

Complements ``test_review_tasks_routes.py`` (happy paths, the English/French 409 title and
the status codes) with the FR and EN problem documents for 403 and 422, the exact 409 body,
and the publication contract: every review route is in the Route_Table, is mounted on the
real app and is published in the OpenAPI document.

**Validates: Requirements 15.2, 15.8**
"""

from __future__ import annotations

from typing import Any

import pytest
from edu_it_api.errors.ai import (
    INVALID_REVIEW_ACTION,
    REVIEW_TASK_ALREADY_DECIDED,
    REVIEWER_EXPERTISE_REQUIRED,
)
from edu_it_api.openapi_export import openapi_document_for_major
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.review_tasks import NewReviewTask, TargetKind, TaskKind
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant

TRACK = "trk-a"
BODY = '{"prompt": {"en": "Q?", "fr": "Q ?"}}'
LOCALES = ("fr", "en")

REVIEW_ROUTES = {
    ("GET", "/review-tasks"),
    ("GET", "/review-tasks/{task_id}"),
    ("POST", "/review-tasks/{task_id}/approve"),
    ("POST", "/review-tasks/{task_id}/reject"),
    ("POST", "/review-tasks/{task_id}/request-changes"),
    ("POST", "/review-tasks/{task_id}/edit"),
}


def _seed(served: Any, name: str = "v1") -> str:
    served.adapters.variants.add(NewVariant(name, "q", TRACK, VariantState.PENDING_REVIEW))
    served.adapters.variants.add_revision(NewRevision(name, "c1", BODY, origin="ai", model="m"))
    task = served.adapters.review_tasks.add(
        NewReviewTask(
            f"task-{name}",
            TaskKind.NEW_VARIANT,
            TargetKind.VARIANT,
            name,
            TRACK,
            revision_content_version="c1",
        )
    )
    return str(task.stable_id)


def _reviewer(served: Any, track: str = TRACK) -> Any:
    browser, _ = served.signed_in()
    served.adapters.roles.set_role(RoleAssignment(served.user_id(browser), ROLE_REVIEWER, track))
    return browser


def _assert_problem(reply: Any, problem_type: Any, locale: str) -> dict[str, Any]:
    assert reply.status == problem_type.status
    assert reply.is_problem
    body = reply.json()
    assert body["type"].endswith(problem_type.slug)
    assert body["status"] == problem_type.status
    assert body["title"] == problem_type.title[locale]
    assert body["detail"]
    return body  # type: ignore[no-any-return]


@pytest.mark.parametrize("locale", LOCALES)
def test_403_problem_document_for_a_reviewer_of_another_track(served: Any, locale: str) -> None:
    task_id = _seed(served)
    outsider = _reviewer(served, track="trk-b")
    reply = outsider.get(f"/v1/review-tasks/{task_id}", query=f"locale={locale}")
    body = _assert_problem(reply, REVIEWER_EXPERTISE_REQUIRED, locale)
    assert body["detail"] == REVIEWER_EXPERTISE_REQUIRED.detail[locale]
    # The task is neither disclosed nor changed.
    assert "Q ?" not in reply.raw.decode()  # the variant body is not disclosed
    assert served.adapters.review_tasks.get(task_id).state.value == "open"


@pytest.mark.parametrize("locale", LOCALES)
def test_403_problem_document_for_a_learner_on_every_review_route(served: Any, locale: str) -> None:
    task_id = _seed(served)
    learner, _ = served.signed_in()
    query = f"locale={locale}"
    base = f"/v1/review-tasks/{task_id}"
    replies = [
        learner.get("/v1/review-tasks", query=query),
        learner.get(base, query=query),
        learner.post(f"{base}/approve", {}, query=query),
        learner.post(f"{base}/reject", {}, query=query),
        learner.post(f"{base}/request-changes", {"comment": "fix"}, query=query),
        learner.post(f"{base}/edit", {"body": {"prompt": {"en": "x", "fr": "y"}}}, query=query),
    ]
    for reply in replies:
        _assert_problem(reply, REVIEWER_EXPERTISE_REQUIRED, locale)
    assert served.adapters.review_tasks.get(task_id).state.value == "open"


@pytest.mark.parametrize("locale", LOCALES)
def test_409_problem_document_when_the_task_is_already_decided(served: Any, locale: str) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    assert reviewer.post(f"/v1/review-tasks/{task_id}/approve", {}).status == 200
    for action, payload in (
        ("approve", {}),
        ("reject", {}),
        ("request-changes", {"comment": "late"}),
    ):
        reply = reviewer.post(
            f"/v1/review-tasks/{task_id}/{action}", payload, query=f"locale={locale}"
        )
        body = _assert_problem(reply, REVIEW_TASK_ALREADY_DECIDED, locale)
        assert body["detail"] == REVIEW_TASK_ALREADY_DECIDED.detail[locale]
        assert body["instance"] == f"/v1/review-tasks/{task_id}/{action}"
    assert served.adapters.review_tasks.get(task_id).state.value == "approved"


@pytest.mark.parametrize("locale", LOCALES)
def test_422_problem_document_for_a_blank_change_request_comment(served: Any, locale: str) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    reply = reviewer.post(
        f"/v1/review-tasks/{task_id}/request-changes", {"comment": "   "}, query=f"locale={locale}"
    )
    body = _assert_problem(reply, INVALID_REVIEW_ACTION, locale)
    # The reason is translated and the placeholder is filled.
    assert "{reason}" not in body["detail"]
    expected = {
        "fr": "Un commentaire est requis pour demander des modifications.",
        "en": "A comment is required to request changes.",
    }[locale]
    assert expected in body["detail"]
    assert served.adapters.review_tasks.get(task_id).state.value == "open"


@pytest.mark.parametrize("locale", LOCALES)
def test_422_problem_document_for_a_malformed_request_body(served: Any, locale: str) -> None:
    task_id = _seed(served)
    reviewer = _reviewer(served)
    bad_requests: list[tuple[str, dict[str, Any]]] = [
        (f"/v1/review-tasks/{task_id}/request-changes", {}),
        (f"/v1/review-tasks/{task_id}/edit", {"body": {}}),
    ]
    for path, payload in bad_requests:
        reply = reviewer.post(path, payload, query=f"locale={locale}")
        assert reply.status == 422
        assert reply.is_problem
        assert reply.json()["status"] == 422
    assert served.adapters.review_tasks.get(task_id).state.value == "open"


def test_unknown_task_is_a_translated_404_problem(served: Any) -> None:
    reviewer = _reviewer(served)
    for locale, title in (
        ("fr", "Tâche de relecture introuvable"),
        ("en", "Review task not found"),
    ):
        reply = reviewer.get("/v1/review-tasks/nope", query=f"locale={locale}")
        assert reply.status == 404
        assert reply.is_problem
        assert reply.json()["title"] == title


# -- Route table and OpenAPI --------------------------------------------------------------


def test_route_table_lists_every_review_route_behind_the_cookie_session() -> None:
    entries = {(e.method, e.path): e for e in ROUTE_TABLE if "/review-tasks" in e.path}
    assert set(entries) == REVIEW_ROUTES
    assert all(e.authentication == "cookie" for e in entries.values())
    assert all(e.condition == "always" for e in entries.values())


def test_openapi_publishes_every_review_route_and_nothing_extra() -> None:
    document = openapi_document_for_major(1)
    published = {
        (method.upper(), path)
        for path, item in document["paths"].items()
        if "/review-tasks" in path
        for method in item
        if method in {"get", "post", "put", "delete", "patch"}
    }
    assert published == REVIEW_ROUTES
    edit = document["paths"]["/review-tasks/{task_id}/edit"]["post"]
    assert "201" in edit["responses"]
    tagged = {
        op["tags"][0]
        for path, item in document["paths"].items()
        if "/review-tasks" in path
        for op in item.values()
    }
    assert tagged == {"review-tasks"}


def test_running_app_serves_the_review_routes_in_its_openapi_document(served: Any) -> None:
    reply = served.browser().get("/v1/openapi.json")
    assert reply.status == 200
    paths = set(reply.json()["paths"])
    assert {path for _, path in REVIEW_ROUTES} <= paths


def test_every_review_route_is_mounted_and_requires_a_session(served: Any) -> None:
    # A mounted route answers an anonymous caller with a problem, never a 404/405.
    anonymous = served.browser()
    for method, path in sorted(REVIEW_ROUTES):
        url = f"/v1{path.replace('{task_id}', 't1')}"
        payload = (
            {
                "approve": {},
                "reject": {},
                "request-changes": {"comment": "c"},
                "edit": {"body": {"k": "v"}},
            }[url.rsplit("/", 1)[-1]]
            if method == "POST"
            else None
        )
        reply = anonymous.get(url) if method == "GET" else anonymous.post(url, payload)
        assert reply.status in {401, 403}, (method, path, reply.status)
        assert reply.is_problem
