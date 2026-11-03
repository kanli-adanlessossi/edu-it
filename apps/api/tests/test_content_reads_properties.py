"""Property test for quiz and project content reads (api-route-wiring task 6.3).

Drives the real ``GetQuizUseCase`` / ``GetProjectUseCase``, ``Content_StoreTextSource`` and
``Content_Router`` over in-memory fakes (catalog and content store).

**Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.7**
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from edu_it_api.content_routes import ContentServices, build_content_router
from edu_it_api.errors.accounts import install_account_error_handlers
from edu_it_api.errors.handlers import install_error_handlers
from edu_it_api.errors.learning import install_learning_error_handlers
from edu_it_api.versioning import build_version_app
from edu_it_application.learning import GetProjectUseCase, GetQuizUseCase
from edu_it_domain.learning.models import QuestionOption, Rubric, RubricCriterion
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_infrastructure.adapters.content_text import Content_StoreTextSource
from edu_it_infrastructure.fakes import In_Memory_CatalogRepository
from fastapi import FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st

_TEXT = st.text(alphabet="abcdefghij klmnopqrstuvwxyz", min_size=1, max_size=12)


@dataclass(frozen=True)
class _Item:
    stable_id: str
    payload: str
    status: str = "active"


class _Store:
    def __init__(self) -> None:
        self.items: dict[str, list[_Item]] = {}

    def add(self, content_type: str, stable_id: str, payload: dict[str, Any]) -> None:
        self.items.setdefault(content_type, []).append(_Item(stable_id, json.dumps(payload)))

    def list_items(self, content_type: str) -> list[_Item]:
        return sorted(self.items.get(content_type, ()), key=lambda i: i.stable_id)


@st.composite
def _localized(draw: st.DrawFn) -> dict[str, str]:
    """English always present; French optional (drives per-field fallback)."""
    value = {"en": draw(_TEXT)}
    if draw(st.booleans()):
        value["fr"] = draw(_TEXT)
    return value


def _pick(value: dict[str, str], locale: str) -> str:
    return value.get(locale, value["en"])


@st.composite
def _quizzes(draw: st.DrawFn) -> dict[str, Any]:
    questions = []
    for _ in range(draw(st.integers(1, 4))):
        n = draw(st.integers(2, 5))
        questions.append(
            {
                "prompt": draw(_localized()),
                "options": [draw(_localized()) for _ in range(n)],
                "correct": draw(st.integers(0, n - 1)),
                "hints": draw(st.lists(_localized(), max_size=3)),
            }
        )
    return {
        "title": draw(_localized()),
        "version": draw(st.text(alphabet="v0123456789.", min_size=2, max_size=6)),
        "questions": questions,
    }


@st.composite
def _projects(draw: st.DrawFn) -> dict[str, Any]:
    return {
        "title": draw(_localized()),
        "description": draw(_localized()),
        "criteria": draw(
            st.lists(st.tuples(_localized(), st.integers(1, 10)), min_size=1, max_size=5)
        ),
    }


def _build(quiz: dict[str, Any], project: dict[str, Any]) -> FastAPI:
    catalog = In_Memory_CatalogRepository()
    store = _Store()

    store.add("quiz", "qz_1", {"title": quiz["title"]})
    questions = []
    for qi, q in enumerate(quiz["questions"]):
        qid = f"qn_{qi:02d}"
        store.add(
            "question",
            qid,
            {"quiz_stable_id": "qz_1", "prompt_text": q["prompt"], "hints": q["hints"]},
        )
        for oi, text in enumerate(q["options"]):
            store.add(
                "question_option",
                f"{qid}_op_{oi:02d}",
                {"question_stable_id": qid, "is_correct": oi == q["correct"], "text": text},
            )
        questions.append(
            QuestionRecord(
                stable_id=qid,
                options=tuple(
                    QuestionOption(option_id=oi + 1, is_correct=oi == q["correct"])
                    for oi in range(len(q["options"]))
                ),
            )
        )
    catalog.add_quiz(
        QuizRecord(
            stable_id="qz_1",
            lesson_id="ls_1",
            content_version=quiz["version"],
            questions=tuple(questions),
        )
    )

    store.add("project", "pj_1", {"title": project["title"], "description": project["description"]})
    store.add("rubric", "rb_1", {"project_stable_id": "pj_1"})
    criteria = []
    for ci, (title, points) in enumerate(project["criteria"]):
        store.add("rubric_criterion", f"rc_{ci:02d}", {"rubric_stable_id": "rb_1", "title": title})
        criteria.append(RubricCriterion(stable_id=f"rc_{ci:02d}", max_points=points))
    catalog.add_rubric(Rubric(stable_id="rb_1", project_id="pj_1", criteria=tuple(criteria)))

    text = Content_StoreTextSource(store)
    services = ContentServices(
        current_user=lambda _request: "usr-1",
        get_quiz=GetQuizUseCase(catalog=catalog, text=text),
        get_project=GetProjectUseCase(catalog=catalog, text=text),
    )
    app = build_version_app(1, [build_content_router(services)])
    install_error_handlers(app)
    install_learning_error_handlers(app)
    install_account_error_handlers(app)
    return app


def _get(app: FastAPI, path: str, query: str) -> tuple[int, Any]:
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


def _keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


def _bools(node: Any) -> list[bool]:
    if isinstance(node, bool):
        return [node]
    if isinstance(node, dict):
        return [b for v in node.values() for b in _bools(v)]
    if isinstance(node, list):
        return [b for v in node for b in _bools(v)]
    return []


# Feature: api-route-wiring, Property 14: Content reads are complete, safe, and localized
@settings(max_examples=100, deadline=None)
@given(quiz=_quizzes(), project=_projects(), locale=st.sampled_from(["fr", "en"]))
def test_content_reads_are_complete_safe_and_localized(
    quiz: dict[str, Any], project: dict[str, Any], locale: str
) -> None:
    app = _build(quiz, project)

    # Quiz: complete, localized per field with English fallback, no correctness flag.
    status, body = _get(app, "/quizzes/qz_1", f"locale={locale}")
    assert status == 200
    assert set(body) == {"stable_id", "title", "content_version", "locale", "questions"}
    assert body["stable_id"] == "qz_1"
    assert body["content_version"] == quiz["version"]
    assert body["title"] == _pick(quiz["title"], locale)
    assert body["locale"] == (locale if locale in quiz["title"] else "en")
    assert len(body["questions"]) == len(quiz["questions"])
    for served, expected in zip(body["questions"], quiz["questions"], strict=True):
        assert set(served) == {"stable_id", "prompt", "options", "hints"}
        assert served["prompt"] == _pick(expected["prompt"], locale)
        assert served["hints"] == [_pick(h, locale) for h in expected["hints"]]
        assert served["options"] == [
            {"option_id": i + 1, "text": _pick(t, locale)}
            for i, t in enumerate(expected["options"])
        ]
    assert not {k for k in _keys(body) if "correct" in k.lower()}
    # Structural check (not a substring scan: generated titles may be "correct"):
    # the key is never exposed as a field, and no boolean flag appears anywhere.
    assert not _bools(body)

    # Project: complete rubric, localized per field with English fallback.
    status, body = _get(app, "/projects/pj_1", f"locale={locale}")
    assert status == 200
    assert set(body) == {"stable_id", "title", "description", "locale", "rubric"}
    assert body["title"] == _pick(project["title"], locale)
    assert body["description"] == _pick(project["description"], locale)
    assert body["locale"] == (locale if locale in project["title"] else "en")
    assert body["rubric"] == [
        {"stable_id": f"rc_{i:02d}", "title": _pick(t, locale), "max_points": p}
        for i, (t, p) in enumerate(project["criteria"])
    ]
