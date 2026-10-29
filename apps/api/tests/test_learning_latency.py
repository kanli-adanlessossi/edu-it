"""p95 latency of the learning endpoints against the Seed_Content (task 24.3).

Drives the real routers and use cases over the SQL repositories (the schema the
Postgres adapters run on, created on an in-memory SQLite engine) with the
shared Redis idempotency adapter on fakeredis. The catalog is populated from the
authored Seed_Content under ``content/`` (not hand-made fixtures), and every
sample is a full in-process ASGI request/response round trip, so the figure
includes routing, validation, the use case, and every repository query.

Each endpoint is sampled ``SAMPLES`` times, each sample as a distinct learner, so
cooldown, idempotency, and hint-level state never short-circuit the measured path.

Tuning loop (Req 29.1): when an endpoint exceeds the budget the failure message
lists every endpoint's p95 and the SQL it issued most, so the result feeds back
into repository/index work (keyset pagination, ``stable_id``-indexed lookups from
task 20.1) instead of ending as a bare assertion. Set ``LATENCY_REPORT`` to a file
path to also write the measured table (for ``docs/reports/phase-2.md``).

**Validates: Requirements 29.1**
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis
import pytest
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
from edu_it_application.learning.attempt_codec import (
    decode_attempt_result,
    encode_attempt_result,
)
from edu_it_content.validation import load_content
from edu_it_domain.learning.models import Mastery, MasteryState, ReviewSchedule
from edu_it_infrastructure.adapters.idempotency_store import Redis_IdempotencyStore
from edu_it_infrastructure.fakes import Fake_ProjectReviewer, Sequential_IdGenerator, Test_Clock
from edu_it_infrastructure.persistence import (
    Postgres_AttemptRepository,
    Postgres_CatalogRepository,
    Postgres_EnrollmentRepository,
    Postgres_LessonRepository,
    Postgres_MasteryRepository,
    Postgres_ProgressRepository,
    Postgres_ReviewRepository,
    metadata,
)
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

SAMPLES = 50
P95_BUDGET_MS = 300.0
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEED_ROOT = _REPO_ROOT / "content"
_T0 = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
_T = metadata.tables

_TRACK = "track.linux-fundamentals"
_QUIZ = "quiz.reading-permissions"
_LESSON = "lesson.reading-permissions"
_CONCEPT = "concept.reading-permissions"
_PROJECT = "project.harden-shared-directory"
_RUBRIC = "rubric.harden-shared-directory"


# --- Seed_Content -> SQL ------------------------------------------------------------


def _seed_catalog(engine: Engine) -> dict[str, Any]:
    """Insert the authored Seed_Content catalog and return the lesson text by stable_id."""
    raw: dict[str, Any] = dict(load_content(_SEED_ROOT).raw_items)
    pk: dict[str, int] = {}

    def provenance(conn: Any, item: dict[str, Any]) -> int:
        p = item["provenance"]
        result = conn.execute(
            insert(_T["content_provenance"]).values(
                stable_id=p["stable_id"], content_version=p["content_version"], origin=p["origin"]
            )
        )
        return int(result.inserted_primary_key[0])

    def put(conn: Any, table: str, stable_id: str, **values: Any) -> None:
        result = conn.execute(insert(_T[table]).values(stable_id=stable_id, **values))
        pk[stable_id] = int(result.inserted_primary_key[0])

    # Every question covers the concept named like its quiz (quiz.x -> concept.x).
    concept_of_quiz = {
        q["provenance"]["stable_id"]: "concept." + q["provenance"]["stable_id"].split(".", 1)[1]
        for q in raw["quiz"]
    }
    lesson_text: dict[str, Any] = {}
    with engine.begin() as c:
        for item in raw["track"]:
            sid = item["provenance"]["stable_id"]
            put(
                c,
                "track",
                sid,
                provenance_id=provenance(c, item),
                title_fr=item["title"]["fr"],
                title_en=item["title"]["en"],
                status=item["status"],
            )
        for item in raw["concept"]:
            sid = item["provenance"]["stable_id"]
            put(
                c,
                "concept",
                sid,
                provenance_id=provenance(c, item),
                title_fr=item["title"]["fr"],
                title_en=item["title"]["en"],
            )
        for edge in raw.get("prerequisite", []):
            c.execute(
                insert(_T["prerequisite"]).values(
                    concept_id=pk[edge["concept_stable_id"]],
                    requires_concept_id=pk[edge["requires_concept_stable_id"]],
                )
            )
        required: dict[str, list[str]] = {}
        for edge in raw.get("prerequisite", []):
            required.setdefault(edge["concept_stable_id"], []).append(
                edge["requires_concept_stable_id"]
            )
        for item in raw["module"]:
            sid = item["provenance"]["stable_id"]
            # A module is gated by the prerequisites of the concept it teaches.
            gate = required.get("concept." + sid.split(".", 1)[1], [])
            put(
                c,
                "module",
                sid,
                track_id=pk[item["track_stable_id"]],
                provenance_id=provenance(c, item),
                ordinal=item["ordinal"],
                prerequisite_concept_ids=json.dumps(gate) if gate else None,
            )
        for item in raw["lesson"]:
            sid = item["provenance"]["stable_id"]
            put(
                c,
                "lesson",
                sid,
                module_id=pk[item["module_stable_id"]],
                provenance_id=provenance(c, item),
                ordinal=item["ordinal"],
            )
            lesson_text[sid] = (
                {"fr": item["title"]["fr"], "en": item["title"]["en"]},
                {"fr": "# " + item["title"]["fr"], "en": "# " + item["title"]["en"]},
            )
        for item in raw["quiz"]:
            sid = item["provenance"]["stable_id"]
            put(
                c,
                "quiz",
                sid,
                lesson_id=pk[item["lesson_stable_id"]],
                provenance_id=provenance(c, item),
            )
        for item in raw["question"]:
            sid = item["provenance"]["stable_id"]
            quiz_sid = item["quiz_stable_id"]
            put(
                c,
                "question",
                sid,
                quiz_id=pk[quiz_sid],
                provenance_id=provenance(c, item),
                prompt_text=item["prompt_text"]["en"],
                concept_stable_ids=json.dumps([concept_of_quiz[quiz_sid]]),
            )
        for opt in raw["question_option"]:
            c.execute(
                insert(_T["question_option"]).values(
                    question_id=pk[opt["question_stable_id"]], is_correct=opt["is_correct"]
                )
            )
        for item in raw["project"]:
            sid = item["provenance"]["stable_id"]
            put(c, "project", sid, provenance_id=provenance(c, item))
        for item in raw["rubric"]:
            put(c, "rubric", item["stable_id"], project_id=pk[item["project_stable_id"]])
        for item in raw["rubric_criterion"]:
            c.execute(
                insert(_T["rubric_criterion"]).values(
                    rubric_id=pk[item["rubric_stable_id"]], max_points=item["max_points"]
                )
            )
    return {
        "lesson_text": lesson_text,
        "criteria": [i["max_points"] for i in raw["rubric_criterion"]],
    }


def _seed_learners(engine: Engine, count: int) -> list[str]:
    ids = [f"usr-perf-{i}" for i in range(count)]
    with engine.begin() as c:
        c.execute(
            insert(_T["user"]),
            [
                {"stable_id": u, "email": f"{u}@e.test", "locale": "en", "status": "verified"}
                for u in ids
            ],
        )
    return ids


# --- In-process ASGI call --------------------------------------------------------------


def _call(
    app: Any,
    method: str,
    path: str,
    *,
    user: str,
    body: Any = None,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"content-type", b"application/json"), (b"x-user", user.encode())]
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

        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload) if payload else None


# --- Fixture -----------------------------------------------------------------------------


class _RequestBoundProgress(Postgres_ProgressRepository):
    """Progress repo that scopes lesson completions to the enrollment just listed.

    ``GetProgressUseCase`` lists module progress for an enrollment and then asks for
    each module's completions; the SQL adapter needs that enrollment to resolve the
    right ``module_progress`` row when several learners share a module.
    """

    def list_module_progress(self, enrollment_id: str) -> Any:
        self._last_enrollment = enrollment_id
        return super().list_module_progress(enrollment_id)

    def list_lesson_completions(self, module_id: str) -> Any:
        return super().for_enrollment(self._last_enrollment).list_lesson_completions(module_id)


class _World:
    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(self.engine, "connect")
        def _fk(dbapi: Any, _record: object) -> None:
            cur = dbapi.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        metadata.create_all(self.engine)
        seeded = _seed_catalog(self.engine)
        self.criteria: list[int] = seeded["criteria"]
        self.users = _seed_learners(self.engine, SAMPLES * 7)

        self.statements: Counter[str] = Counter()

        @event.listens_for(self.engine, "before_cursor_execute")
        def _count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
            self.statements[" ".join(statement.split())[:160]] += 1

        clock = Test_Clock(start=_T0)
        ids = Sequential_IdGenerator("id")
        redis = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        self.catalog = Postgres_CatalogRepository(self.engine)
        self.reviews = Postgres_ReviewRepository(self.engine)
        enrollments = Postgres_EnrollmentRepository(self.engine)
        progress = _RequestBoundProgress(self.engine)
        mastery = self.mastery = Postgres_MasteryRepository(self.engine)
        attempts = Postgres_AttemptRepository(self.engine)
        text = seeded["lesson_text"]
        services = LearningServices(
            current_user=lambda r: r.headers["x-user"],
            enroll=EnrollUseCase(
                catalog=self.catalog,
                enrollments=enrollments,
                progress=progress,
                mastery=mastery,
                clock=clock,
                ids=ids,
            ),
            progress=GetProgressUseCase(
                catalog=self.catalog, enrollments=enrollments, progress=progress, mastery=mastery
            ),
            lessons=GetLessonUseCase(
                lessons=Postgres_LessonRepository(self.engine, lambda s: text[s])
            ),
            attempt_quiz=AttemptQuizUseCase(
                catalog=self.catalog,
                attempts=attempts,
                mastery=mastery,
                reviews=self.reviews,
                idempotency=Redis_IdempotencyStore(
                    redis, encode=encode_attempt_result, decode=decode_attempt_result
                ),
                clock=clock,
                ids=ids,
            ),
            reveal_hint=RevealHintUseCase(catalog=self.catalog, mastery=mastery, clock=clock),
            start_review=StartReviewUseCase(
                catalog=self.catalog, reviews=self.reviews, clock=clock
            ),
            submit_review=SubmitReviewUseCase(
                catalog=self.catalog, reviews=self.reviews, mastery=mastery, clock=clock
            ),
            submit_project=SubmitProjectUseCase(
                catalog=self.catalog,
                attempts=attempts,
                reviewer=Fake_ProjectReviewer(default_points=10, pending=True),
                ids=ids,
            ),
        )
        self.app = build_version_app(1, [build_learning_router(services)])
        self.clock = clock

    def make_due(self, user: str) -> None:
        self.mastery.save(
            Mastery(user_id=user, concept_id=_CONCEPT, score=60, state=MasteryState.PRACTICED)
        )
        self.reviews.save(
            ReviewSchedule(
                user_id=user,
                concept_id=_CONCEPT,
                due_at=_T0 - timedelta(days=1),
                interval=timedelta(days=1),
            )
        )

    def correct_selections(self, quiz_id: str) -> dict[str, int]:
        quiz = self.catalog.get_quiz(quiz_id)
        assert quiz is not None
        return {
            q.stable_id: next(o.option_id for o in q.options if o.is_correct)
            for q in quiz.questions
        }


@pytest.fixture(scope="module")
def world() -> Iterator[_World]:
    w = _World()
    try:
        yield w
    finally:
        w.engine.dispose()


# --- Measurement ---------------------------------------------------------------------------


def p95(samples_ms: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(samples_ms)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _measure(
    users: list[str], request: Callable[[str, int], tuple[int, Any]], ok: set[int]
) -> list[float]:
    samples: list[float] = []
    for index, user in enumerate(users):
        start = time.perf_counter()
        status, body = request(user, index)
        samples.append((time.perf_counter() - start) * 1000.0)
        assert status in ok, (status, body)
    return samples


def _endpoint_runs(w: _World) -> dict[str, list[float]]:
    users = iter([w.users[i : i + SAMPLES] for i in range(0, len(w.users), SAMPLES)])
    enrol_u, progress_u, lesson_u, quiz_u, hint_u, review_u, project_u = (
        next(users) for _ in range(7)
    )
    app = w.app
    results: dict[str, list[float]] = {}

    results["enrollment"] = _measure(
        enrol_u,
        lambda u, _i: _call(app, "POST", "/enrollments", user=u, body={"track_id": _TRACK}),
        {200, 201},
    )

    for u in progress_u:
        _call(app, "POST", "/enrollments", user=u, body={"track_id": _TRACK})
    results["progress"] = _measure(
        progress_u,
        lambda u, _i: _call(app, "GET", "/progress", user=u, query=f"track_id={_TRACK}"),
        {200},
    )

    results["lesson"] = _measure(
        lesson_u,
        lambda u, _i: _call(app, "GET", f"/lessons/{_LESSON}", user=u, query="locale=fr"),
        {200},
    )

    selections = w.correct_selections(_QUIZ)
    results["quiz-attempt"] = _measure(
        quiz_u,
        lambda u, i: _call(
            app,
            "POST",
            "/quiz-attempts",
            user=u,
            body={"quiz_id": _QUIZ, "selections": selections},
            headers={"Idempotency-Key": f"perf-{i}"},
        ),
        {200},
    )

    question = next(iter(selections))
    results["hint"] = _measure(
        hint_u,
        lambda u, _i: _call(
            app, "POST", "/hints", user=u, body={"quiz_id": _QUIZ, "question_id": question}
        ),
        {200},
    )

    for u in review_u:
        w.make_due(u)
    pool = {
        q.stable_id: next(o.option_id for o in q.options if o.is_correct)
        for q in w.catalog.list_questions_for_concept(_CONCEPT)
    }
    review_get = _measure(
        review_u,
        lambda u, _i: _call(app, "GET", "/reviews", user=u, query=f"concept_id={_CONCEPT}"),
        {200},
    )
    review_post = _measure(
        review_u,
        lambda u, _i: _call(
            app, "POST", "/reviews", user=u, body={"concept_id": _CONCEPT, "selections": pool}
        ),
        {200},
    )
    results["review"] = review_get + review_post

    self_assessment = {f"{_RUBRIC}#{n}": min(points, 5) for n, points in enumerate(w.criteria, 1)}
    results["project-submission"] = _measure(
        project_u,
        lambda u, _i: _call(
            app,
            "POST",
            "/project-submissions",
            user=u,
            body={
                "project_id": _PROJECT,
                "self_assessment": self_assessment,
                "evidence": ["https://example.test/evidence"],
            },
        ),
        {201},
    )
    return results


def test_seed_content_endpoint_p95_under_300_ms(world: _World) -> None:
    """**Validates: Requirements 29.1** — p95 < 300 ms on every learning endpoint."""
    results = _endpoint_runs(world)
    table = {name: p95(samples) for name, samples in results.items()}

    lines = ["| endpoint | samples | p95 (ms) | max (ms) |", "|---|---|---|---|"]
    lines += [
        f"| {n} | {len(results[n])} | {table[n]:.2f} | {max(results[n]):.2f} |" for n in table
    ]
    report = "\n".join(lines)
    print("\n" + report)
    target = os.environ.get("LATENCY_REPORT")
    if target:
        Path(target).write_text(report + "\n", encoding="utf-8")

    over = {n: v for n, v in table.items() if v >= P95_BUDGET_MS}
    hot = "\n".join(f"{count:5d}x {sql}" for sql, count in world.statements.most_common(10))
    assert not over, (
        f"p95 over {P95_BUDGET_MS:.0f} ms for {over}.\n{report}\n"
        "Tune the repository/index path (keyset pagination, stable_id-indexed lookups, "
        f"task 20.1) for the most-issued statements:\n{hot}"
    )
