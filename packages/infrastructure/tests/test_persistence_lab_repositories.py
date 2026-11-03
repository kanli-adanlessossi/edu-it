"""Tests for the PostgreSQL lab repositories (hands-on-labs task 5.2).

Run on in-memory SQLite built from the shared ``metadata`` (foreign keys on). The
same scenarios run against the in-memory fakes to prove parity (task 2.1).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
    TaskCompletion,
)
from edu_it_domain.ports.lab_sessions import (
    ActiveSessionExists,
    GradingRunRepository,
    LabSessionRepository,
)
from edu_it_infrastructure.fakes.lab_repositories import (
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
)
from edu_it_infrastructure.persistence import (
    Postgres_GradingRunRepository,
    Postgres_LabSessionRepository,
    metadata,
)
from sqlalchemy import create_engine, event, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool, StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_T = metadata.tables
_GAP = timedelta(seconds=10)

_PG_ENV = "EDU_IT_TEST_POSTGRES_URL"

Pair = tuple[LabSessionRepository, GradingRunRepository]


def _seed(eng: Engine) -> None:
    with eng.begin() as c:
        for sid in ("u1", "u2"):
            c.execute(
                insert(_T["user"]).values(
                    stable_id=sid, email=f"{sid}@e.test", locale="en", status="verified"
                )
            )
        c.execute(
            insert(_T["content_provenance"]).values(
                stable_id="prov-1", content_version="v1", origin="ai"
            )
        )
        c.execute(
            insert(_T["track"]).values(
                stable_id="t1", provenance_id=1, title_fr="T", title_en="T", status="active"
            )
        )
        c.execute(
            insert(_T["module"]).values(
                stable_id="m1",
                track_id=1,
                provenance_id=1,
                ordinal=1,
                prerequisite_concept_ids=None,
            )
        )
        c.execute(
            insert(_T["lesson"]).values(stable_id="l1", module_id=1, provenance_id=1, ordinal=1)
        )
        c.execute(insert(_T["lab"]).values(stable_id="lab1", lesson_id=1, provenance_id=1))
        c.execute(
            insert(_T["lab_task"]),
            [
                {"stable_id": "task1", "lab_id": 1, "ordinal": 1},
                {"stable_id": "task2", "lab_id": 1, "ordinal": 2},
            ],
        )


def _fk_on(eng: Engine) -> None:
    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection: Any, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    _fk_on(eng)
    metadata.create_all(eng)
    _seed(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def file_engine(tmp_path: Any) -> Iterator[Engine]:
    """On-disk SQLite with one connection per thread, for real concurrency."""
    eng = create_engine(
        f"sqlite:///{tmp_path / 'labs.db'}", connect_args={"timeout": 30}, poolclass=NullPool
    )
    _fk_on(eng)
    metadata.create_all(eng)
    _seed(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    """Real PostgreSQL named by ``EDU_IT_TEST_POSTGRES_URL``; skipped when unset/unreachable.

    The target database is wiped (``drop_all``) before and after each test, so it
    must be a throwaway database.
    """
    url = os.environ.get(_PG_ENV)
    if not url:
        pytest.skip(f"{_PG_ENV} not set; PostgreSQL contract run skipped")
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect():
            pass
    except OperationalError:
        eng.dispose()
        pytest.skip("PostgreSQL unavailable; contract run skipped")
    metadata.drop_all(eng)
    metadata.create_all(eng)
    _seed(eng)
    try:
        yield eng
    finally:
        metadata.drop_all(eng)
        eng.dispose()


def _postgres(engine: Engine) -> Pair:
    return Postgres_LabSessionRepository(engine), Postgres_GradingRunRepository(engine)


def _memory(_engine: Engine) -> Pair:
    runs = In_Memory_GradingRunRepository()
    return In_Memory_LabSessionRepository(runs), runs


@pytest.fixture(params=["sqlite", "postgres", "memory"])
def repos(request: pytest.FixtureRequest) -> Pair:
    """The same contract suite over SQLite, real PostgreSQL (if available), and the fakes."""
    if request.param == "memory":
        return _memory(None)  # type: ignore[arg-type]
    fixture = "engine" if request.param == "sqlite" else "pg_engine"
    return _postgres(request.getfixturevalue(fixture))


def _session(
    sid: str = "s1",
    user: str = "u1",
    status: SessionStatus = SessionStatus.PROVISIONING,
    at: datetime = _T0,
) -> LabSession:
    return LabSession(
        stable_id=sid,
        user_id=user,
        lab_id="lab1",
        status=status,
        created_at=at,
        ttl_at=at + timedelta(hours=1),
    )


def _run(rid: str = "r1", sid: str = "s1") -> GradingRun:
    return GradingRun(
        stable_id=rid,
        session_id=sid,
        status=RunStatus.RUNNING,
        accepted_at=_T0,
        deadline_at=_T0 + timedelta(seconds=15),
    )


def _result(check: str, task: str = "task1") -> CheckResult:
    return CheckResult(check, task, CheckOutcome.PASSED, "ok", f"fb.{check}")


def test_ports_conformance(engine: Engine) -> None:
    sessions, runs = _postgres(engine)
    assert isinstance(sessions, LabSessionRepository)
    assert isinstance(runs, GradingRunRepository)


def test_add_get_and_one_active_per_user(repos: Pair) -> None:
    sessions, _ = repos
    sessions.add_if_no_active(_session("s1"))
    assert sessions.get("s1") == _session("s1")
    assert sessions.get_for_owner("s1", "u1") is not None
    assert sessions.get_for_owner("s1", "u2") is None
    assert sessions.get("nope") is None
    assert sessions.get_active_for_user("u1") is not None
    with pytest.raises(ActiveSessionExists):
        sessions.add_if_no_active(_session("s2"))
    sessions.add_if_no_active(_session("s3", user="u2"))
    assert sessions.transition("s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.FAILED)
    assert sessions.get_active_for_user("u1") is None
    sessions.add_if_no_active(_session("s2"))


def test_transition_is_compare_and_set(repos: Pair) -> None:
    sessions, _ = repos
    sessions.add_if_no_active(_session())
    ref = {"sandbox_ref": "box-1", "last_activity_at": _T0}
    assert sessions.transition(
        "s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.READY, **ref
    )
    assert not sessions.transition(
        "s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.FAILED
    )
    got = sessions.get("s1")
    assert got is not None
    assert (got.status, got.sandbox_ref, got.last_activity_at) == (
        SessionStatus.READY,
        "box-1",
        _T0,
    )
    assert not sessions.transition("zzz", expected={SessionStatus.READY}, to=SessionStatus.ENDED)


def test_try_accept_grading_rate_limit(repos: Pair) -> None:
    sessions, _ = repos
    sessions.add_if_no_active(_session(status=SessionStatus.READY))
    assert sessions.try_accept_grading("s1", now=_T0, min_interval=_GAP)
    assert not sessions.try_accept_grading("s1", now=_T0, min_interval=_GAP)  # not ready
    sessions.transition("s1", expected={SessionStatus.GRADING}, to=SessionStatus.READY)
    assert not sessions.try_accept_grading("s1", now=_T0 + _GAP, min_interval=_GAP)  # exactly 10 s
    assert sessions.try_accept_grading(
        "s1", now=_T0 + _GAP + timedelta(milliseconds=1), min_interval=_GAP
    )
    got = sessions.get("s1")
    assert got is not None and got.status is SessionStatus.GRADING


def test_claim_provision_attempt(repos: Pair) -> None:
    sessions, _ = repos
    sessions.add_if_no_active(_session())
    assert sessions.claim_provision_attempt("s1", expected_attempts=0, now=_T0)
    assert not sessions.claim_provision_attempt("s1", expected_attempts=0, now=_T0)
    got = sessions.get("s1")
    assert got is not None and got.provision_attempts == 1
    sessions.transition("s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.READY)
    assert not sessions.claim_provision_attempt("s1", expected_attempts=1, now=_T0)


def test_touch_activity_never_moves_backwards_and_counts(repos: Pair) -> None:
    sessions, _ = repos
    sessions.add_if_no_active(_session())
    sessions.touch_activity("s1", _T0 + timedelta(minutes=5))
    sessions.touch_activity("s1", _T0 + timedelta(minutes=1))
    got = sessions.get("s1")
    assert got is not None and got.last_activity_at == _T0 + timedelta(minutes=5)
    assert sessions.count_started_since("u1", _T0) == 1
    assert sessions.count_started_since("u1", _T0 + timedelta(seconds=1)) == 0
    assert sessions.count_started_since("u2", _T0) == 0


def test_keyset_pagination_by_created_at_and_stable_id(repos: Pair) -> None:
    sessions, _ = repos
    ended = SessionStatus.ENDED
    for sid in ("sb", "sa", "sc"):  # sa and sb share an instant: tie-break by stable_id
        at = _T0 + timedelta(minutes=1 if sid == "sc" else 0)
        sessions.add_if_no_active(_session(sid, status=SessionStatus.PROVISIONING, at=at))
        sessions.transition(sid, expected={SessionStatus.PROVISIONING}, to=ended)
    first = sessions.list_for_user_page("u1", limit=2)
    assert [s.stable_id for s in first] == ["sa", "sb"]
    last = first[-1]
    rest = sessions.list_for_user_page("u1", after=(last.created_at, last.stable_id), limit=2)
    assert [s.stable_id for s in rest] == ["sc"]


def test_list_active_and_ended_before_and_delete(repos: Pair) -> None:
    sessions, runs = repos
    sessions.add_if_no_active(_session("s1"))
    sessions.add_if_no_active(_session("s2", user="u2"))
    sessions.transition(
        "s2", expected={SessionStatus.PROVISIONING}, to=SessionStatus.EXPIRED, ended_at=_T0
    )
    assert [s.stable_id for s in sessions.list_active()] == ["s1"]
    assert [s.stable_id for s in sessions.list_ended_before(_T0 + timedelta(seconds=1))] == ["s2"]
    assert sessions.list_ended_before(_T0) == ()
    runs.add(_run("r1", "s2"))
    runs.add_task_completions("r1", [TaskCompletion("u2", "task1", "r1", _T0, True)])
    assert sessions.delete_session_data(["s2", "missing"]) == 1
    assert sessions.get("s2") is None and runs.get("r1") is None
    assert not runs.first_pass_exists("u2", "task1")
    assert sessions.delete_session_data([]) == 0


def test_run_creation_and_idempotent_results(repos: Pair) -> None:
    sessions, runs = repos
    sessions.add_if_no_active(_session())
    runs.add(_run())
    with pytest.raises(ValueError):
        runs.add(_run())
    got = runs.get("r1")
    assert got == _run()
    assert (got.accepted_at, got.deadline_at) == (_T0, _T0 + timedelta(seconds=15))
    runs.append_result("r1", _result("c1"))
    runs.append_result("r1", _result("c2"))
    runs.append_result("r1", _result("c1"))
    got = runs.get("r1")
    assert got is not None
    assert [r.check_id for r in got.results] == ["c1", "c2"]
    assert got.results[0] == _result("c1")
    assert runs.get("nope") is None


def test_finish_is_cas_and_list_running(repos: Pair) -> None:
    sessions, runs = repos
    sessions.add_if_no_active(_session())
    runs.add(_run())
    assert [r.stable_id for r in runs.list_running()] == ["r1"]
    later = _T0 + timedelta(seconds=3)
    assert runs.finish("r1", status=RunStatus.PASSED, graded_at=later)
    assert not runs.finish("r1", status=RunStatus.FAILED, graded_at=later)
    got = runs.get("r1")
    assert got is not None and (got.status, got.graded_at) == (RunStatus.PASSED, later)
    assert runs.list_running() == ()


def test_task_completions_are_idempotent_per_user_and_task(repos: Pair) -> None:
    sessions, runs = repos
    sessions.add_if_no_active(_session())
    runs.add(_run("r1"))
    assert not runs.first_pass_exists("u1", "task1")
    row = TaskCompletion("u1", "task1", "r1", _T0, True)
    runs.add_task_completions("r1", [row, TaskCompletion("u1", "task2", "r1", _T0, False)])
    assert runs.first_pass_exists("u1", "task1")
    assert runs.first_pass_exists("u1", "task2")
    assert not runs.first_pass_exists("u2", "task1")
    sessions.transition("s1", expected={SessionStatus.PROVISIONING}, to=SessionStatus.ENDED)
    sessions.add_if_no_active(_session("s2"))
    runs.add(_run("r2", "s2"))
    # A later pass inserts its own uncounted row; re-delivery within a run stays idempotent.
    repeat = TaskCompletion("u1", "task1", "r2", _T0, False)
    runs.add_task_completions("r2", [repeat])
    runs.add_task_completions("r2", [repeat])
    if isinstance(runs, Postgres_GradingRunRepository):
        assert [(c.task_id, c.counted_for_mastery) for c in runs.completions_for_run("r2")] == [
            ("task1", False)
        ]
        first = runs.completions_for_run("r1")
        assert [(c.task_id, c.counted_for_mastery) for c in first] == [
            ("task1", True),
            ("task2", False),
        ]


# --------------------------------------------------------------------------
# Concurrency (Req 7.1, 11.8): threads against the engine and the in-memory fake.
# --------------------------------------------------------------------------

_THREADS = 8


@pytest.fixture(params=["sqlite-file", "postgres", "memory"])
def conc_repos(request: pytest.FixtureRequest) -> Pair:
    if request.param == "memory":
        return _memory(None)  # type: ignore[arg-type]
    fixture = "file_engine" if request.param == "sqlite-file" else "pg_engine"
    return _postgres(request.getfixturevalue(fixture))


def _race(work: Callable[[int], object]) -> list[object]:
    """Run ``work(i)`` on ``_THREADS`` threads released together; return outcomes."""
    barrier = threading.Barrier(_THREADS)
    out: list[object] = [None] * _THREADS

    def target(i: int) -> None:
        barrier.wait(timeout=30)
        try:
            out[i] = work(i)
        except Exception as exc:
            out[i] = exc

    threads = [threading.Thread(target=target, args=(i,)) for i in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return out


def test_concurrent_starts_yield_one_active_session(conc_repos: Pair) -> None:
    sessions, _ = conc_repos
    outcomes = _race(lambda i: sessions.add_if_no_active(_session(f"s{i}")))
    winners = [o for o in outcomes if o is None]
    losers = [o for o in outcomes if isinstance(o, ActiveSessionExists)]
    assert len(winners) == 1
    assert len(losers) == _THREADS - 1
    assert len(sessions.list_active()) == 1


def test_concurrent_transition_has_one_cas_winner(conc_repos: Pair) -> None:
    sessions, _ = conc_repos
    sessions.add_if_no_active(_session(status=SessionStatus.READY))
    outcomes = _race(
        lambda _i: sessions.transition("s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED)
    )
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == _THREADS - 1
    got = sessions.get("s1")
    assert got is not None and got.status is SessionStatus.ENDED


def test_concurrent_grading_accept_and_provision_claim_have_one_winner(
    conc_repos: Pair,
) -> None:
    sessions, _ = conc_repos
    sessions.add_if_no_active(_session(status=SessionStatus.READY))
    accepted = _race(lambda _i: sessions.try_accept_grading("s1", now=_T0, min_interval=_GAP))
    assert accepted.count(True) == 1 and accepted.count(False) == _THREADS - 1

    sessions.add_if_no_active(_session("s2", user="u2"))
    claimed = _race(lambda _i: sessions.claim_provision_attempt("s2", expected_attempts=0, now=_T0))
    assert claimed.count(True) == 1 and claimed.count(False) == _THREADS - 1
    got = sessions.get("s2")
    assert got is not None and got.provision_attempts == 1


def test_concurrent_finish_has_one_winner(conc_repos: Pair) -> None:
    sessions, runs = conc_repos
    sessions.add_if_no_active(_session())
    runs.add(_run())
    outcomes = _race(lambda _i: runs.finish("r1", status=RunStatus.PASSED, graded_at=_T0))
    assert outcomes.count(True) == 1 and outcomes.count(False) == _THREADS - 1


def test_run_deadline_round_trip_preserves_instants(repos: Pair) -> None:
    sessions, runs = repos
    sessions.add_if_no_active(_session())
    accepted = datetime(2024, 3, 5, 8, 30, 15, 123000, tzinfo=UTC)
    deadline = accepted + timedelta(seconds=15)
    runs.add(
        GradingRun(
            stable_id="r9",
            session_id="s1",
            status=RunStatus.RUNNING,
            accepted_at=accepted,
            deadline_at=deadline,
        )
    )
    got = runs.get("r9")
    assert got is not None
    assert got.accepted_at == accepted and got.deadline_at == deadline
    assert got.accepted_at.utcoffset() == timedelta(0)
    assert got.deadline_at - got.accepted_at == timedelta(seconds=15)


# --------------------------------------------------------------------------
# Personal-data coverage (Req 24.5): no new personal table escapes the registry.
# --------------------------------------------------------------------------

_PRE_LAB_UNREGISTERED = frozenset({"content_provenance", "review_queue_item", "variant_revision"})
_LAB_PERSONAL = ("lab_session", "lab_run", "lab_task_completion")


def test_personal_lab_tables_are_registered_and_none_is_unregistered() -> None:
    from edu_it_domain.ports.personal_data import PERSONAL_DATA_TABLES

    assert set(_LAB_PERSONAL) <= set(PERSONAL_DATA_TABLES)
    # Every table referencing ``user`` must be registered, except the two pre-lab tables that
    # only reference it as an author/operator (not learner data); no lab table may join them.
    user_owned = {
        table.name
        for table in metadata.tables.values()
        if any(fk.column.table.name == "user" for fk in table.foreign_keys)
    }
    unregistered = user_owned - set(PERSONAL_DATA_TABLES)
    assert unregistered <= _PRE_LAB_UNREGISTERED, sorted(unregistered)
    # Every registered table exists in the schema, and the lab content tables are not personal.
    assert set(PERSONAL_DATA_TABLES) <= set(metadata.tables)
    assert not {"lab", "lab_task", "lab_check"} & set(PERSONAL_DATA_TABLES)
