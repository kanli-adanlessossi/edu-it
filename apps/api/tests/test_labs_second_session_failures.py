"""Failure-scenario tests for a second session or grading too often (task 8.9).

Drives the labs API in-process over the Fake_SandboxProvider, the in-memory
repositories and a ``Test_Clock``; every number comes from ``domain/config.py``.

**Validates: Requirements 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.7**
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import MutableMapping
from datetime import timedelta
from typing import Any

from edu_it_api.errors import install_error_handlers
from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.versioning import build_version_app
from edu_it_application.labs import (
    EndSessionUseCase,
    GetSessionUseCase,
    ListSessionsUseCase,
    RequestGradingUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabUseCase,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain import config
from edu_it_domain.labs.models import RunStatus, SandboxSpec, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_IdempotencyStore,
    In_Memory_LabSessionRepository,
    In_Memory_MasteryRepository,
    In_Memory_TaskQueue,
    Sequential_IdGenerator,
    Test_Clock,
)


def _lab(lab_id: str) -> LabDefinition:
    return LabDefinition(
        lab_id=lab_id,
        kind="code",
        image="img",
        concept_ids=("con-1",),
        tasks=(
            LabTaskDefinition("t1", required=True, text={"en": "Do", "fr": "Faire"}, hint_count=1),
        ),
        checks=(),
        text={"en": lab_id, "fr": lab_id},
        learner_files=("main.py",),
    )


async def _no_sleep(_seconds: float) -> None:
    return None


class _Env:
    def __init__(self) -> None:
        self.clock = Test_Clock()
        runs = self.runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(runs)
        self.queue = In_Memory_TaskQueue()
        self.sandbox = Fake_SandboxProvider(self.clock)
        catalog = Fake_LabCatalog([_lab("lab1"), _lab("lab2")])
        idem = In_Memory_IdempotencyStore()
        end = EndSessionUseCase(sessions=self.sessions, queue=self.queue, clock=self.clock)
        start = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=self.queue,
            idempotency=idem,
            clock=self.clock,
            ids=Sequential_IdGenerator("ses"),
        )
        services = LabServices(
            current_user=lambda r: r.headers["x-user"],
            catalog=catalog,
            start=start,
            reset=ResetLabUseCase(sessions=self.sessions, end=end, start=start),
            end=end,
            get_session=GetSessionUseCase(sessions=self.sessions),
            list_sessions=ListSessionsUseCase(sessions=self.sessions),
            save_files=SaveFilesUseCase(
                sessions=self.sessions,
                catalog=catalog,
                sandbox=self.sandbox,
                clock=self.clock,
                provider_name="fake",
            ),
            reveal_hint=RevealLabHintUseCase(
                sessions=self.sessions,
                catalog=catalog,
                mastery=In_Memory_MasteryRepository(),
                clock=self.clock,
            ),
            hint_text=lambda _lab, _task, _level, _loc: None,
            cursor_codec=CursorCodec(self.clock),
            clock=self.clock,
            request_grading=RequestGradingUseCase(
                sessions=self.sessions,
                runs=runs,
                queue=self.queue,
                idempotency=idem,
                clock=self.clock,
                ids=Sequential_IdGenerator("run"),
            ),
            runs=runs,
            feedback_messages=lambda _lab, _loc: {},
            sleep=_no_sleep,
        )
        self.app = build_version_app(1, [build_labs_router(services)])
        install_error_handlers(self.app)
        self._keys = 0

    def call(
        self,
        method: str,
        path: str,
        *,
        user: str = "u1",
        body: Any = None,
        key: str | None = None,
        lang: str = "en",
    ) -> tuple[int, dict[str, str], Any]:
        if key is None and method == "POST":
            self._keys += 1
            key = f"auto-{self._keys}"
        headers = {"x-user": user, "accept-language": lang}
        if key is not None:
            headers["idempotency-key"] = key
        return _call(self.app, method, path, body, headers)

    def start(self, lab: str = "lab1", user: str = "u1") -> tuple[int, dict[str, str], Any]:
        return self.call("POST", "/lab-sessions", user=user, body={"lab_id": lab})

    def make_ready(self, session_id: str) -> None:
        spec = SandboxSpec(
            session_id, "img", "learner", False, 1, 1, 1, 1, True, True, True, (), ()
        )
        handle = self.sandbox.provision(spec)
        assert self.sessions.transition(
            session_id,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.READY,
            sandbox_ref=handle.sandbox_id,
        )

    def finish_run(self, session_id: str, run_id: str) -> None:
        assert self.runs.finish(run_id, status=RunStatus.PASSED, graded_at=self.clock.now())
        assert self.sessions.transition(
            session_id, expected={SessionStatus.GRADING}, to=SessionStatus.READY
        )

    def active_sessions(self) -> list[Any]:
        return [s for s in self.sessions.list_active()]

    def provision_jobs(self) -> int:
        return len(self.queue.pending_for("lab.provision"))


def _call(
    app: Any, method: str, path: str, body: Any, headers: dict[str, str]
) -> tuple[int, dict[str, str], Any]:
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"content-type", b"application/json")]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
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
    start = next(m for m in sent if m["type"] == "http.response.start")
    out_headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], out_headers, json.loads(payload) if payload else None


# --- second start (Req 30.1) -----------------------------------------------------


def test_second_start_of_same_lab_is_409_naming_it_and_creates_nothing() -> None:
    env = _Env()
    assert env.start("lab1")[0] == 201
    jobs = env.provision_jobs()
    status, _, problem = env.start("lab1")
    assert status == 409
    assert "lab1" in problem["detail"]
    assert len(env.active_sessions()) == 1
    assert env.provision_jobs() == jobs


def test_start_of_a_different_lab_is_409_naming_the_running_lab() -> None:
    env = _Env()
    assert env.start("lab1")[0] == 201
    jobs = env.provision_jobs()
    status, _, problem = env.start("lab2")
    assert status == 409
    assert "lab1" in problem["detail"]
    assert "lab2" not in problem["detail"]
    assert len(env.active_sessions()) == 1
    assert env.provision_jobs() == jobs


def test_conflict_detail_is_translated() -> None:
    env = _Env()
    env.start("lab1")
    _, _, fr = env.call("POST", "/lab-sessions", body={"lab_id": "lab2"}, lang="fr")
    assert "déjà en cours" in fr["detail"]
    assert "lab1" in fr["detail"]


# --- concurrent starts (Req 30.2) ------------------------------------------------


def test_concurrent_starts_create_exactly_one_session() -> None:
    env = _Env()
    n = 8
    barrier = threading.Barrier(n)
    results: list[int] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()
        status, _, _ = env.start("lab1" if i % 2 else "lab2")
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [201] + [409] * (n - 1)
    assert len(env.active_sessions()) == 1
    assert env.provision_jobs() == 1


# --- provisioning rate limit (Req 30.3) ------------------------------------------


def test_sixth_start_in_window_is_429_with_retry_after_then_allowed() -> None:
    env = _Env()
    for _ in range(config.LAB_PROVISION_RATE_LIMIT):
        status, _, data = env.start()
        assert status == 201
        env.clock.advance(timedelta(seconds=1))
        assert env.call("DELETE", f"/lab-sessions/{data['stable_id']}")[0] == 200

    status, headers, _ = env.start()
    assert status == 429
    retry = int(headers["retry-after"])
    assert 0 < retry <= config.LAB_PROVISION_RATE_WINDOW.total_seconds()
    assert len(env.active_sessions()) == 0

    env.clock.advance(timedelta(seconds=retry))
    assert env.start()[0] == 201


# --- grading too often (Req 30.4, 30.5) ------------------------------------------


def _ready_session(env: _Env) -> str:
    sid = str(env.start()[2]["stable_id"])
    env.make_ready(sid)
    return sid


def test_grading_at_exactly_min_interval_is_429_with_retry_after() -> None:
    env = _Env()
    sid = _ready_session(env)
    status, _, run = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 202
    env.finish_run(sid, run["stable_id"])

    env.clock.advance(config.LAB_GRADING_MIN_INTERVAL - timedelta(seconds=4))
    status, headers, _ = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 429
    assert headers["retry-after"] == "4"

    env.clock.advance(timedelta(seconds=4))  # exactly the minimum interval
    status, headers, _ = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 429
    assert int(headers["retry-after"]) >= 1

    env.clock.advance(timedelta(milliseconds=1))
    assert env.call("POST", f"/lab-sessions/{sid}/grading-runs")[0] == 202


def test_grading_while_running_is_409_and_starts_no_second_run() -> None:
    env = _Env()
    sid = _ready_session(env)
    status, _, run = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 202
    env.clock.advance(config.LAB_GRADING_MIN_INTERVAL + timedelta(seconds=1))

    status, headers, problem = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 409
    assert "retry-after" not in headers
    assert problem["status"] == 409
    assert [r.stable_id for r in env.runs.list_running()] == [run["stable_id"]]
    assert len(env.queue.pending_for("lab.grade")) == 1


# --- foreign owner (Req 30.6) ----------------------------------------------------


def test_foreign_owner_is_indistinguishable_from_missing() -> None:
    env = _Env()
    sid = _ready_session(env)
    run_status, _, run = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert run_status == 202
    rid = run["stable_id"]

    requests = [
        ("GET", "{s}", None),
        ("DELETE", "{s}", None),
        ("POST", "{s}/grading-runs", None),
        ("POST", "{s}/reset", None),
        ("PUT", "{s}/files", {"files": {"main.py": "x"}}),
        ("GET", "{s}/grading-runs/" + rid, None),
    ]
    for method, template, body in requests:
        foreign = env.call(method, "/lab-sessions/" + template.format(s=sid), user="u2", body=body)
        missing = env.call(
            method, "/lab-sessions/" + template.format(s="ses-missing"), user="u2", body=body
        )
        assert foreign[0] == missing[0] == 404, (method, template)

        # Identical apart from the echoed request path (`instance`).
        def strip(d: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in d.items() if k != "instance"}

        assert strip(foreign[2]) == strip(missing[2]), (method, template)

    assert env.sessions.get(sid).status is SessionStatus.GRADING  # type: ignore[union-attr]
