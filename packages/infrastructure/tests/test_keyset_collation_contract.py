"""Keyset pagination breaks instant ties in code-point order on every SQL backend.

The ports and in-memory adapters order ties by ``stable_id`` with Python's ``str``
comparison (code-point order). PostgreSQL's default collation is locale-aware
(en_US: case and punctuation are ignored first), so the SQL paginators must pin the
bytewise ``"C"`` collation there. One suite runs on SQLite always and on PostgreSQL
when ``DATABASE_URL`` is set. Ids are unique per test, so a shared database never
collides and nothing needs cleaning up.

Validates: Requirements 17.5
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.labs.models import LabSession, SessionStatus
from edu_it_domain.learning.models import QuizAttempt
from edu_it_domain.ports.enrollment import EnrollmentRecord
from edu_it_infrastructure.persistence import (
    Postgres_AttemptRepository,
    Postgres_CatalogRepository,
    Postgres_EnrollmentRepository,
    Postgres_LabSessionRepository,
    metadata,
)
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_T = metadata.tables
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_POSTGRES_URL = _DATABASE_URL if _DATABASE_URL.startswith("postgresql") else ""

# Code-point order: B < _x < a < y10 < y2. en_US order would be a < B < _x < y10 < y2.
_SUFFIXES = ("y2", "a", "_x", "B", "y10")

_BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(not _POSTGRES_URL, reason="DATABASE_URL (PostgreSQL) not set"),
    ),
]


@dataclass
class _Env:
    engine: Engine
    ns: str

    def sid(self, label: str) -> str:
        return f"{self.ns}-{label}"


def _insert_id(engine: Engine, table: str, **values: object) -> int:
    with engine.begin() as c:
        key = c.execute(insert(_T[table]).values(**values)).inserted_primary_key
        assert key is not None
        return int(key[0])


@pytest.fixture(params=_BACKENDS)
def env(request: pytest.FixtureRequest) -> Iterator[_Env]:
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    else:
        engine = create_engine(_POSTGRES_URL)
    metadata.create_all(engine)
    try:
        yield _Env(engine, uuid.uuid4().hex[:8])
    finally:
        engine.dispose()


def _seed_user(e: _Env) -> int:
    return _insert_id(
        e.engine, "user", stable_id=e.sid("u"), email=f"{e.sid('u')}@e.test", locale="en",
        status="verified",
    )  # fmt: skip


def _seed_provenance(e: _Env) -> int:
    return _insert_id(
        e.engine, "content_provenance", stable_id=e.sid("prov"), content_version="v1", origin="ai"
    )


def _seed_track(e: _Env, label: str, prov: int) -> int:
    return _insert_id(
        e.engine, "track", stable_id=e.sid(label), provenance_id=prov, title_fr="T", title_en="T",
        status="active",
    )  # fmt: skip


def _seed_lesson(e: _Env, prov: int) -> int:
    track = _seed_track(e, "t", prov)
    module = _insert_id(
        e.engine, "module", stable_id=e.sid("m"), track_id=track, provenance_id=prov, ordinal=1,
        prerequisite_concept_ids=None,
    )  # fmt: skip
    return _insert_id(
        e.engine, "lesson", stable_id=e.sid("l"), module_id=module, provenance_id=prov, ordinal=1
    )


def _walk(page: object, key: object, limit: int = 2) -> list[str]:
    """Follow ``after`` cursors to the end, returning stable ids in served order."""
    seen: list[str] = []
    after = None
    while True:
        rows = page(after=after, limit=limit)  # type: ignore[operator]
        if not rows:
            return seen
        seen.extend(r.stable_id for r in rows)
        after = key(rows[-1])  # type: ignore[operator]


def test_enrollment_page_ties_use_code_point_order(env: _Env) -> None:
    prov = _seed_provenance(env)
    _seed_user(env)
    repo = Postgres_EnrollmentRepository(env.engine)
    for suffix in _SUFFIXES:
        _seed_track(env, f"t-{suffix}", prov)
        repo.add(EnrollmentRecord(env.sid(suffix), env.sid("u"), env.sid(f"t-{suffix}"), _T0))
    expected = sorted(env.sid(s) for s in _SUFFIXES)
    seen = _walk(
        lambda after, limit: repo.list_for_user_page(env.sid("u"), after=after, limit=limit),
        lambda r: (r.enrolled_at, r.stable_id),
    )
    assert seen == expected


def test_quiz_attempt_page_ties_use_code_point_order(env: _Env) -> None:
    prov = _seed_provenance(env)
    _seed_user(env)
    lesson = _seed_lesson(env, prov)
    _insert_id(env.engine, "quiz", stable_id=env.sid("q"), lesson_id=lesson, provenance_id=prov)
    repo = Postgres_AttemptRepository(env.engine)
    for suffix in _SUFFIXES:
        attempt = QuizAttempt(env.sid(suffix), env.sid("q"), 80, False, _T0, "v1")
        repo.add_quiz_attempt(env.sid("u"), attempt, ())
    expected = sorted(env.sid(s) for s in _SUFFIXES)
    seen = _walk(
        lambda after, limit: repo.list_quiz_attempts_page(
            env.sid("u"), env.sid("q"), after=after, limit=limit
        ),
        lambda r: (r.attempted_at, r.stable_id),
    )
    assert seen == expected


def test_lab_session_page_ties_use_code_point_order(env: _Env) -> None:
    prov = _seed_provenance(env)
    _seed_user(env)
    lesson = _seed_lesson(env, prov)
    _insert_id(env.engine, "lab", stable_id=env.sid("lab"), lesson_id=lesson, provenance_id=prov)
    repo = Postgres_LabSessionRepository(env.engine)
    for suffix in _SUFFIXES:  # one active session per learner: end each before the next
        repo.add_if_no_active(
            LabSession(
                stable_id=env.sid(suffix),
                user_id=env.sid("u"),
                lab_id=env.sid("lab"),
                status=SessionStatus.PROVISIONING,
                created_at=_T0,
                ttl_at=_T0 + timedelta(hours=1),
            )
        )
        repo.transition(
            env.sid(suffix), expected={SessionStatus.PROVISIONING}, to=SessionStatus.ENDED
        )
    expected = sorted(env.sid(s) for s in _SUFFIXES)
    seen = _walk(
        lambda after, limit: repo.list_for_user_page(env.sid("u"), after=after, limit=limit),
        lambda r: (r.created_at, r.stable_id),
    )
    assert seen == expected


def test_questions_for_concept_use_code_point_order(env: _Env) -> None:
    prov = _seed_provenance(env)
    lesson = _seed_lesson(env, prov)
    quiz = _insert_id(
        env.engine, "quiz", stable_id=env.sid("q"), lesson_id=lesson, provenance_id=prov
    )
    concept = env.sid("c")
    for suffix in _SUFFIXES:
        _insert_id(
            env.engine, "question", stable_id=env.sid(suffix), quiz_id=quiz, provenance_id=prov,
            prompt_text="?", concept_stable_ids=json.dumps([concept]),
        )  # fmt: skip
    found = Postgres_CatalogRepository(env.engine).list_questions_for_concept(concept)
    assert [q.stable_id for q in found] == sorted(env.sid(s) for s in _SUFFIXES)
