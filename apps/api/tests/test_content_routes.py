"""Route tests for the Content_Router (api-route-wiring task 6.2).

**Validates: Requirements 10.1, 10.3, 10.5**
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from edu_it_api.content_routes import ContentServices, build_content_router
from edu_it_api.errors.accounts import install_account_error_handlers
from edu_it_api.errors.handlers import install_error_handlers
from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_api.identity import NotAuthenticated
from edu_it_api.versioning import build_version_app
from edu_it_application.learning import GetProjectUseCase, GetQuizUseCase
from edu_it_domain.learning.models import QuestionOption, Rubric, RubricCriterion
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.content_text import ProjectText, QuestionText, QuizText
from edu_it_infrastructure.fakes import In_Memory_CatalogRepository
from fastapi import FastAPI, Request


class _Text:
    def quiz_text(self, quiz_id: str, locale: str) -> QuizText | None:
        if quiz_id != "qz_1":
            return None
        return QuizText(
            locale=locale,
            title=f"Quiz {locale}",
            questions=(
                QuestionText(
                    question_id="qn_1",
                    prompt="Which?",
                    option_texts={"1": "a", "2": "b"},
                    hints=("h1",),
                ),
            ),
        )

    def project_text(self, project_id: str, locale: str) -> ProjectText | None:
        if project_id != "pj_1":
            return None
        return ProjectText(locale=locale, title="Proj", brief="Brief", criterion_titles=("First",))


def _app(*, authenticated: bool = True) -> FastAPI:
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
                        QuestionOption(option_id=1, is_correct=True),
                        QuestionOption(option_id=2, is_correct=False),
                    ),
                ),
            ),
        )
    )
    catalog.add_rubric(
        Rubric(
            stable_id="rb_1",
            project_id="pj_1",
            criteria=(RubricCriterion(stable_id="rb_1#1", max_points=4),),
        )
    )
    text = _Text()

    def current_user(_request: Request) -> str:
        if not authenticated:
            raise NotAuthenticated
        return "usr-1"

    services = ContentServices(
        current_user=current_user,
        get_quiz=GetQuizUseCase(catalog=catalog, text=text),
        get_project=GetProjectUseCase(catalog=catalog, text=text),
    )
    app = build_version_app(1, [build_content_router(services)])
    install_error_handlers(app)
    install_learning_error_handlers(app)
    install_account_error_handlers(app)
    return app


def _get(app: FastAPI, path: str, query: str = "") -> tuple[int, Any]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(payload)


def test_quiz_route_returns_text_and_no_correctness() -> None:
    status, body = _get(_app(), "/quizzes/qz_1", "locale=fr")
    assert status == 200
    assert body["title"] == "Quiz fr"
    assert body["content_version"] == "v1"
    question = body["questions"][0]
    assert question["prompt"] == "Which?"
    assert question["hints"] == ["h1"]
    assert question["options"] == [
        {"option_id": 1, "text": "a"},
        {"option_id": 2, "text": "b"},
    ]
    assert "correct" not in json.dumps(body)


def test_project_route_returns_rubric() -> None:
    status, body = _get(_app(), "/projects/pj_1", "locale=en")
    assert status == 200
    assert body["description"] == "Brief"
    assert body["rubric"] == [{"stable_id": "rb_1#1", "title": "First", "max_points": 4}]


def test_unknown_items_return_404_problem() -> None:
    for path in ("/quizzes/nope", "/projects/nope"):
        status, body = _get(_app(), path)
        assert status == 404
        assert body["status"] == 404


def test_invalid_locale_is_rejected() -> None:
    status, _ = _get(_app(), "/quizzes/qz_1", "locale=de")
    assert status == 422


def test_unauthenticated_requests_return_401() -> None:
    for path in ("/quizzes/qz_1", "/projects/pj_1"):
        status, _ = _get(_app(authenticated=False), path)
        assert status == 401
