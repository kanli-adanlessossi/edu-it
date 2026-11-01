"""Example tests for the grading-run endpoints and the result stream (task 7.5).

Reuses the in-process ASGI environment of ``test_labs_routes``.

**Validates: Requirements 11.6, 17.1, 17.2, 17.4, 18.1, 18.2, 18.3, 18.5**
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus

_SPEC = importlib.util.spec_from_file_location(
    "_labs_routes_env", Path(__file__).with_name("test_labs_routes.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_Env: Any = _MODULE._Env


def _raw(env: _Env, path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            await asyncio.Event().wait()  # the client stays connected
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await env.app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    hdrs = {k.decode(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], hdrs, body


def _events(body: bytes) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for block in body.decode().strip().split("\n\n"):
        if not block:
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((fields["id"], fields["event"], json.loads(fields["data"])))
    return out


def _graded_run(env: _Env) -> tuple[str, str]:
    sid = env.start()["stable_id"]
    env.make_ready(sid)
    status, run = env.call("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
    assert status == 202, run
    rid = run["stable_id"]
    for check, outcome in (("c1", CheckOutcome.PASSED), ("c2", CheckOutcome.FAILED)):
        env.runs.append_result(
            rid,
            CheckResult(check, "t1", outcome, "code", "fb.pass" if check == "c1" else "fb.none"),
        )
    assert env.runs.finish(rid, status=RunStatus.FAILED, graded_at=env.clock.now())
    return sid, rid


def test_post_grading_run_is_202_with_location_and_replays() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    env.make_ready(sid)
    status, run = env.call("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
    assert status == 202
    assert run["status"] == "running"
    assert run["session_id"] == sid
    assert run["results"] == []
    status2, run2 = env.call("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
    assert status2 == 202
    assert run2["stable_id"] == run["stable_id"]


def test_post_grading_run_requires_idempotency_key() -> None:
    env = _Env()
    sid = env.start()["stable_id"]
    env.make_ready(sid)
    status, _ = env.call("POST", f"/lab-sessions/{sid}/grading-runs")
    assert status == 422


def test_get_run_returns_learner_view_and_hides_other_users() -> None:
    env = _Env()
    sid, rid = _graded_run(env)
    status, run = env.call("GET", f"/lab-sessions/{sid}/grading-runs/{rid}")
    assert status == 200
    assert run["status"] == "failed"
    assert run["results"][0] == {
        "task_id": "t1",
        "check_id": "c1",
        "outcome": "passed",
        "feedback_key": "fb.pass",
        "message": "Well done",
    }
    assert run["results"][1]["message"] == ""
    assert "outcome_code" not in json.dumps(run)
    status, _ = env.call("GET", f"/lab-sessions/{sid}/grading-runs/{rid}", user="u2")
    assert status == 404
    status, _ = env.call("GET", f"/lab-sessions/{sid}/grading-runs/nope")
    assert status == 404


def test_events_stream_results_then_run() -> None:
    env = _Env()
    sid, rid = _graded_run(env)
    status, hdrs, body = _raw(
        env, f"/lab-sessions/{sid}/grading-runs/{rid}/events", {"x-user": "u1"}
    )
    assert status == 200
    assert hdrs["content-type"].startswith("text/event-stream")
    events = _events(body)
    assert [(i, e) for i, e, _ in events] == [("1", "result"), ("2", "result"), ("3", "run")]
    assert events[0][2]["message"] == "Well done"
    assert events[2][2]["status"] == "failed"
    assert "outcome_code" not in body.decode()


def test_events_resume_with_last_event_id() -> None:
    env = _Env()
    sid, rid = _graded_run(env)
    path = f"/lab-sessions/{sid}/grading-runs/{rid}/events"
    _, _, body = _raw(env, path, {"x-user": "u1", "last-event-id": "1"})
    assert [(i, e) for i, e, _ in _events(body)] == [("2", "result"), ("3", "run")]
    _, _, body = _raw(env, path, {"x-user": "u1", "last-event-id": "3"})
    assert _events(body) == []


def test_events_for_other_user_is_404() -> None:
    env = _Env()
    sid, rid = _graded_run(env)
    status, _, _ = _raw(env, f"/lab-sessions/{sid}/grading-runs/{rid}/events", {"x-user": "u2"})
    assert status == 404
