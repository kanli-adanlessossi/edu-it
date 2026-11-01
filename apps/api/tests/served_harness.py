"""Shared in-process harness for tests of the mounted ``/v1`` app (api-route-wiring task 8).

``Served`` composes the Served_App exactly as ``serve.build_served_app`` does, but over the
in-memory fakes, and ``Browser`` is a minimal ASGI client with a cookie jar that follows the
CSRF double-submit protocol (the project pins no HTTP client). Tests load this module with
``importlib`` (the suite runs in ``importlib`` import mode) and
get instances through the ``served`` and ``shared_served`` fixtures of ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from edu_it_api.serve import ServedApp, compose_served_app
from edu_it_content.lab_runtime import load_synced_store
from edu_it_domain.learning.models import QuestionOption, Rubric, RubricCriterion
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_infrastructure.composition import CompositionSettings

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "content"
TRACK = "track.linux-fundamentals"
QUIZ = "quiz.changing-permissions"
PROJECT = "project.harden-shared-directory"
PASSWORD = "correct horse battery staple"
SESSION_COOKIE = "edu_it_session"
CSRF_COOKIE = "csrf_token"


@dataclass
class Reply:
    """One HTTP response."""

    status: int
    headers: list[tuple[str, str]]
    raw: bytes

    def json(self) -> Any:
        return json.loads(self.raw) if self.raw else None

    def header(self, name: str) -> str | None:
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def header_list(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    @property
    def is_problem(self) -> bool:
        return (self.header("content-type") or "").startswith("application/problem+json")


@dataclass
class Browser:
    """An in-process client with a cookie jar and automatic CSRF double-submit."""

    app: Any
    jar: dict[str, str] = field(default_factory=dict)
    origin: str | None = None

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        csrf: bool = True,
        locale: str = "en",
    ) -> Reply:
        unsafe = method.upper() not in {"GET", "HEAD", "OPTIONS"}
        if unsafe and csrf and CSRF_COOKIE not in self.jar:
            self.request("GET", "/v1/auth/csrf")
        sent_headers = {"accept-language": locale, **(headers or {})}
        if body is not None:
            sent_headers["content-type"] = "application/json"
        if unsafe and csrf and CSRF_COOKIE in self.jar:
            sent_headers.setdefault("x-csrf-token", self.jar[CSRF_COOKIE])
        if self.origin is not None:
            sent_headers["origin"] = self.origin
        if self.jar:
            sent_headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        return self._call(method.upper(), path, query, body, sent_headers)

    def get(self, path: str, **kw: Any) -> Reply:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Reply:
        return self.request("POST", path, {} if body is None else body, **kw)

    def _call(
        self, method: str, path: str, query: str, body: Any, headers: dict[str, str]
    ) -> Reply:
        raw = b"" if body is None else json.dumps(body).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
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
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await self.app(scope, receive, send)

        asyncio.run(run())
        start = next(m for m in sent if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        hdrs = [(k.decode(), v.decode()) for k, v in start["headers"]]
        for key, value in hdrs:
            if key.lower() == "set-cookie":
                parsed: SimpleCookie = SimpleCookie()
                parsed.load(value)
                for name, morsel in parsed.items():
                    if morsel["max-age"] == "0" or morsel.value == "":
                        self.jar.pop(name, None)
                    else:
                        self.jar[name] = morsel.value
        return Reply(start["status"], hdrs, data)


class Served:
    """The Served_App built over the in-memory fakes, plus helpers to drive it."""

    def __init__(self) -> None:
        self.composed: ServedApp = compose_served_app(
            CompositionSettings(), fakes=True, content_root=CONTENT_ROOT
        )
        self.app = self.composed.app
        self.adapters = self.composed.adapters
        self._emails = itertools.count(1)
        self.seed_catalog()

    # -- catalog -----------------------------------------------------------------------
    def seed_catalog(self) -> None:
        """Copy the seed track, one quiz, and one rubric from the content into the fakes."""
        import json as _json

        store, _ = load_synced_store(CONTENT_ROOT)
        catalog: Any = self.adapters.catalog
        for item in store.list_items("module"):
            payload = _json.loads(item.payload)
            catalog.add_module(
                ModuleRecord(
                    stable_id=item.stable_id,
                    track_id=payload["track_stable_id"],
                    ordinal=payload["ordinal"],
                )
            )
        options: dict[str, list[tuple[str, bool]]] = {}
        for item in sorted(store.list_items("question_option"), key=lambda i: i.stable_id):
            payload = _json.loads(item.payload)
            options.setdefault(payload["question_stable_id"], []).append(
                (item.stable_id, bool(payload["is_correct"]))
            )
        questions = tuple(
            QuestionRecord(
                stable_id=item.stable_id,
                options=tuple(
                    QuestionOption(option_id=rank, is_correct=correct)
                    for rank, (_, correct) in enumerate(options.get(item.stable_id, ()), start=1)
                ),
            )
            for item in store.list_items("question")
            if _json.loads(item.payload)["quiz_stable_id"] == QUIZ
        )
        lesson = next(
            _json.loads(i.payload)["lesson_stable_id"]
            for i in store.list_items("quiz")
            if i.stable_id == QUIZ
        )
        catalog.add_quiz(
            QuizRecord(stable_id=QUIZ, lesson_id=lesson, questions=questions, content_version="v1")
        )
        criteria = tuple(
            RubricCriterion(stable_id=i.stable_id, max_points=_json.loads(i.payload)["max_points"])
            for i in store.list_items("rubric_criterion")
        )
        catalog.add_rubric(
            Rubric(
                stable_id="rubric.harden-shared-directory", project_id=PROJECT, criteria=criteria
            )
        )
        self.questions = questions
        self.criteria = criteria

    # -- accounts ----------------------------------------------------------------------
    def browser(self, origin: str | None = None) -> Browser:
        return Browser(self.app, origin=origin)

    def new_email(self) -> str:
        return f"learner{next(self._emails)}@example.test"

    def verification_token(self, email: str) -> str:
        mailer: Any = self.adapters.mailer
        return str(mailer.sent_to(email)[-1].headers["X-Verification-Token"])

    def sign_up(self, browser: Browser, email: str, password: str = PASSWORD) -> Reply:
        return browser.post("/v1/auth/sign-up", {"email": email, "password": password})

    def verify(self, browser: Browser, email: str) -> Reply:
        return browser.post("/v1/auth/verify-email", {"token": self.verification_token(email)})

    def sign_in(self, browser: Browser, email: str, password: str = PASSWORD) -> Reply:
        return browser.post("/v1/auth/sign-in", {"email": email, "password": password})

    def signed_in(self, email: str | None = None) -> tuple[Browser, str]:
        """A browser with an authenticated session and the user's email."""
        address = email or self.new_email()
        browser = self.browser()
        assert self.sign_up(browser, address).status == 202
        assert self.verify(browser, address).status == 200
        assert self.sign_in(browser, address).status == 200
        return browser, address

    def user_id(self, browser: Browser) -> str:
        reply = browser.get("/v1/auth/session")
        assert reply.status == 200
        return str(reply.json()["user_id"])
