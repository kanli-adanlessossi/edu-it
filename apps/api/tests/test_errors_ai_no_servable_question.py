"""Problem document for ``no-servable-question`` (ai-content task 14.2).

**Validates: Requirements 14.5, 20.4**
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from edu_it_api.errors import install_error_handlers
from edu_it_domain.ai.serving import NoServableQuestionError
from fastapi import FastAPI


def _app(exc: Exception) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return app


def _get(app: FastAPI, language: str) -> tuple[int, dict[str, str], dict[str, Any]]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/boom",
        "raw_path": b"/boom",
        "query_string": b"",
        "headers": [(b"accept-language", language.encode())],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[dict[str, Any]] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, json.loads(body)


@pytest.mark.parametrize(
    ("language", "title", "detail_start"),
    [
        ("fr", "Aucune question disponible", "Aucune question approuvée"),
        ("en", "No question available", "No approved question"),
    ],
)
def test_no_servable_question_is_a_translated_404(
    language: str, title: str, detail_start: str
) -> None:
    status, headers, body = _get(_app(NoServableQuestionError("con-secret")), language)

    assert status == 404
    assert headers["content-type"].startswith("application/problem+json")
    assert body["type"].endswith("no-servable-question")
    assert body["title"] == title
    assert body["detail"].startswith(detail_start)
    assert "con-secret" not in str(body)
