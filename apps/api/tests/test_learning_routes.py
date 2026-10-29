"""Tests for the learning routers under /v1 (task 18.1).

Drives the real routers, use cases, and domain services over the infrastructure
fakes, through a minimal in-process ASGI call (the project pins no HTTP client).
The router is mounted on a bare versioned sub-app so the CSRF/error middleware of
the full app (other tasks) is not under test here.

**Validates: Requirements 17.1, 17.2, 17.3, 17.4, 18.1, 18.3, 18.4, 18.5**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from edu_it_api.learning import LearningServices, build_learning_router
from edu_it_api.versioning import build_version_app
from edu_it_application.learning import (
    AttemptQuizUseCase,
    EnrollUseCase,
    GetLessonUseCase,
    GetProgressUseCase,
    RevealHintUseCase,
    StartReviewUseCase,
    SubmitProjectUseCase,
    SubmitReviewUseCase,
)
from edu_it_domain.learning.models import QuestionOption, Rubric, RubricCriterion
from edu_it_domain.ports.catalog import ModuleRecord, QuestionRecord, QuizRecord
from edu_it_domain.ports.lessons import LessonRecord, LessonRepository
from edu_it_infrastructure.fakes import (
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_IdempotencyStore,
    In_Memory_MasteryRepository,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from fastapi import FastAPI

_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_USER = "usr-lea"
_TRACK = "trk-linux"
_QUIZ = "qz-1"
_PROJECT = "prj-1"


class _Lessons(LessonRepository):
    def __init__(self) -> None:
        self.items = {
            "les-1": LessonRecord(
                stable_id="les-1",
                module_id="mod-1",
                titles={"en": "Perms", "fr": "Droits"},
                bodies={"en": "english", "fr": "francais"},
                quiz_id=_QUIZ,
                content_version="v1",
            ),
            "les-en": LessonRecord(
                stable_id="les-en", module_id="mod-1", titles={"en": "T"}, bodies={"en": "only"}
            ),
        }

    def get_lesson(self, stable_id: str) -> LessonRecord | None:
        return self.items.get(stable_id)


def _app(reviews: In_Memory_ReviewRepository | None = None) -> FastAPI:
    clock = Test_Clock(start=_T0)
    ids = Sequential_IdGenerator("id")
    catalog = In_Memory_CatalogRepository()
    enrollments = In_Memory_EnrollmentRepository()
    progress = In_Memory_ProgressRepository()
    mastery = In_Memory_MasteryRepository()
    reviews = reviews if reviews is not None else In_Memory_ReviewRepository()
    attempts = In_Memory_AttemptRepository()
    catalog.add_module(ModuleRecord(stable_id="mod-1", track_id=_TRACK, ordinal=1))
    catalog.add_quiz(
        QuizRecord(
            stable_id=_QUIZ,
            lesson_id="les-1",
            questions=(
                QuestionRecord(
                    stable_id="qn-1",
                    options=(
                        QuestionOption(option_id=1, is_correct=True),
                        QuestionOption(option_id=2, is_correct=False),
                    ),
                    concept_ids=("con-1",),
                ),
            ),
            content_version="v1",
        )
    )
    catalog.add_rubric(
        Rubric(
            stable_id="rub-1",
            project_id=_PROJECT,
            criteria=(RubricCriterion(stable_id="crit-1", max_points=10),),
        )
    )
    services = LearningServices(
        current_user=lambda _r: _USER,
        enroll=EnrollUseCase(
            catalog=catalog,
            enrollments=enrollments,
            progress=progress,
            mastery=mastery,
            clock=clock,
            ids=ids,
        ),
        progress=GetProgressUseCase(
            catalog=catalog, enrollments=enrollments, progress=progress, mastery=mastery
        ),
        lessons=GetLessonUseCase(lessons=_Lessons()),
        attempt_quiz=AttemptQuizUseCase(
            catalog=catalog,
            attempts=attempts,
            mastery=mastery,
            reviews=reviews,
            idempotency=In_Memory_IdempotencyStore(),
            clock=clock,
            ids=ids,
        ),
        reveal_hint=RevealHintUseCase(catalog=catalog, mastery=mastery, clock=clock),
        start_review=StartReviewUseCase(catalog=catalog, reviews=reviews, clock=clock),
        submit_review=SubmitReviewUseCase(
            catalog=catalog, reviews=reviews, mastery=mastery, clock=clock
        ),
        submit_project=SubmitProjectUseCase(
            catalog=catalog,
            attempts=attempts,
            reviewer=Fake_ProjectReviewer(default_points=10, pending=True),
            ids=ids,
        ),
    )
    return build_version_app(1, [build_learning_router(services)])


def _call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"content-type", b"application/json")]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": hdrs,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        done = False

        async def receive() -> dict[str, Any]:
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload) if payload else None


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        out = set(value)
        for v in value.values():
            out |= _walk_keys(v)
        return out
    if isinstance(value, list):
        return set().union(*(_walk_keys(v) for v in value)) if value else set()
    return set()


def test_enroll_then_progress() -> None:
    app = _app()
    status, body = _call(app, "POST", "/enrollments", body={"track_id": _TRACK})
    assert status == 201
    assert body["track_id"] == _TRACK
    again, again_body = _call(app, "POST", "/enrollments", body={"track_id": _TRACK})
    assert again == 200
    assert again_body["stable_id"] == body["stable_id"]

    status, progress = _call(app, "GET", "/progress", query=f"track_id={_TRACK}")
    assert status == 200
    assert progress["modules"][0]["module_id"] == "mod-1"
    assert progress["modules"][0]["state"] == "unlocked"


def test_lesson_by_stable_id_with_english_fallback() -> None:
    app = _app()
    status, body = _call(app, "GET", "/lessons/les-1", query="locale=fr")
    assert (status, body["locale"], body["body"]) == (200, "fr", "francais")
    status, body = _call(app, "GET", "/lessons/les-en", query="locale=fr")
    assert (status, body["locale"], body["body"]) == (200, "en", "only")


def test_quiz_attempt_requires_idempotency_key_and_replays() -> None:
    app = _app()
    payload = {"quiz_id": _QUIZ, "selections": {"qn-1": 1}}
    status, _ = _call(app, "POST", "/quiz-attempts", body=payload)
    assert status == 422  # missing Idempotency-Key header

    headers = {"Idempotency-Key": "k-1"}
    status, first = _call(app, "POST", "/quiz-attempts", body=payload, headers=headers)
    assert status == 200
    assert first["attempt"]["score"] == 100
    assert first["replayed"] is False
    status, second = _call(app, "POST", "/quiz-attempts", body=payload, headers=headers)
    assert status == 200
    assert second["replayed"] is True
    assert second["attempt"]["stable_id"] == first["attempt"]["stable_id"]


def test_hint_reveals_progressive_levels() -> None:
    app = _app()
    payload = {"quiz_id": _QUIZ, "question_id": "qn-1"}
    _, first = _call(app, "POST", "/hints", body=payload)
    _, second = _call(app, "POST", "/hints", body=payload)
    assert (first["hint_level"], second["hint_level"]) == (1, 2)


def test_project_submission_is_pending_until_reviewed() -> None:
    app = _app()
    body = {
        "project_id": _PROJECT,
        "self_assessment": {"crit-1": 8},
        "evidence": ["https://example.test/evidence"],
    }
    status, result = _call(app, "POST", "/project-submissions", body=body)
    assert status == 201
    assert result["status"] == "pending"
    assert result["review_outcome"] is None


def test_review_without_schedule_is_not_served_as_questions() -> None:
    # No review is scheduled until a concept reaches Practiced; the route
    # surfaces the application error for the errors module (18.4) to map.
    from edu_it_application.learning import ReviewNotAvailableError

    app = _app()
    try:
        _call(app, "GET", "/reviews", query="concept_id=con-1")
    except ReviewNotAvailableError:
        return
    raise AssertionError("expected ReviewNotAvailableError to propagate")


def test_review_below_minimum_pool_is_declined_with_problem_and_no_schedule_change() -> None:
    """**Validates: Requirements 28.1, 28.2** (the seeded concept has 1 < 3 questions)."""
    from datetime import timedelta

    from edu_it_api.errors.learning import install_learning_error_handlers
    from edu_it_domain.config import MIN_ELIGIBLE_QUESTION_COUNT
    from edu_it_domain.learning.models import ReviewSchedule

    reviews = In_Memory_ReviewRepository()
    seeded = ReviewSchedule(
        user_id=_USER,
        concept_id="con-1",
        due_at=_T0 - timedelta(days=1),  # already due, so only the pool blocks it
        interval=timedelta(days=1),
    )
    reviews.save(seeded)
    app = _app(reviews)
    install_learning_error_handlers(app)
    assert MIN_ELIGIBLE_QUESTION_COUNT > 1

    status, body = _call(app, "GET", "/reviews", query="concept_id=con-1")
    assert status == 409
    assert body["type"].endswith("/insufficient-review-pool")
    assert body["status"] == 409

    status, body = _call(
        app, "POST", "/reviews", body={"concept_id": "con-1", "selections": {"qn-1": 1}}
    )
    assert status == 409
    assert body["type"].endswith("/insufficient-review-pool")

    assert reviews.get(_USER, "con-1") == seeded  # not created, advanced, or extended
    assert len(reviews.list_for_user(_USER)) == 1


def test_no_internal_numeric_keys_exposed() -> None:
    app = _app()
    _, enrolled = _call(app, "POST", "/enrollments", body={"track_id": _TRACK})
    _, progress = _call(app, "GET", "/progress", query=f"track_id={_TRACK}")
    _, attempt = _call(
        app,
        "POST",
        "/quiz-attempts",
        body={"quiz_id": _QUIZ, "selections": {"qn-1": 1}},
        headers={"Idempotency-Key": "k"},
    )
    keys = _walk_keys(enrolled) | _walk_keys(progress) | _walk_keys(attempt)
    assert "id" not in keys
    assert not {k for k in keys if k.endswith("_pk") or k == "pk"}
    # every identifier-valued field is an opaque string
    assert isinstance(enrolled["stable_id"], str)
    assert isinstance(attempt["attempt"]["stable_id"], str)


def test_routes_published_under_expected_paths() -> None:
    app = _app()
    paths = set(app.openapi()["paths"])
    assert {
        "/enrollments",
        "/progress",
        "/lessons/{stable_id}",
        "/quiz-attempts",
        "/hints",
        "/reviews",
        "/project-submissions",
    } <= paths
