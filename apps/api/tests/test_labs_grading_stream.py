"""Tests for the grading result stream and learner-facing responses (task 7.6).

Covers SSE ordering (every per-Check ``result`` before the final ``run`` event, also when
results arrive across polls), resume with ``Last-Event-ID`` at every position, and that raw
Check output never reaches a response or stream payload. The leak test drives the real
``GradeRun`` / ``RecordCheckResult`` path over ``Fake_SandboxProvider``. The stream sleep is
injected and the clock is ``Test_Clock``, so nothing waits.

**Validates: Requirements 18.1, 18.3, 18.6**
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from edu_it_api.errors import install_error_handlers
from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.versioning import build_version_app
from edu_it_application.labs import (
    EndSessionUseCase,
    GetSessionUseCase,
    GradeRun,
    ListSessionsUseCase,
    RecordCheckResult,
    RequestGradingUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabUseCase,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain import config
from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus, SessionStatus
from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_domain.ports.lab_catalog import LabCheckDefinition, LabDefinition, LabTaskDefinition
from edu_it_domain.ports.sandbox import ExecResult
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

# Reuse the in-process ASGI helpers of the sibling test modules.
_here = Path(__file__).parent


def _load(name: str, file: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _here / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_grading_routes = _load("_labs_grading_routes_env", "test_labs_grading_routes.py")
_raw = _grading_routes._raw
_events = _grading_routes._events
_call = _load("_labs_routes_env", "test_labs_routes.py")._call

MARKER = "SECRET-MARKER-7f3a9c1e-do-not-leak"
_INTERVAL = config.LAB_RESULT_POLL_INTERVAL.total_seconds()


def _lab(kind: str) -> LabDefinition:
    return LabDefinition(
        lab_id="lab1",
        kind=kind,
        image="img",
        concept_ids=("cn1",),
        tasks=(LabTaskDefinition("t1", required=True, text={"en": "Do it"}),),
        checks=(
            LabCheckDefinition("c1", "t1", 5.0, {"pass": "fb.ok"}),
            LabCheckDefinition("c2", "t1", 5.0),
        ),
        text={"en": "Lab one"},
        learner_files=("main.py",),
        bundle_path="lab1",
    )


class _Env:
    """API + use cases + GradeRun over in-memory fakes, with a scriptable stream sleep."""

    def __init__(self, tmp_path: Path, kind: str = "terminal") -> None:
        self.lab = _lab(kind)
        (tmp_path / "lab1" / "sandbox" / "checks").mkdir(parents=True)
        (tmp_path / "lab1" / "expected").mkdir()
        self.clock = Test_Clock()
        self.runs = runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(runs)
        self.sandbox = Fake_SandboxProvider(self.clock)
        self.mastery = In_Memory_MasteryRepository()
        self.on_sleep: Callable[[], None] | None = None
        self.sleeps: list[float] = []
        queue = In_Memory_TaskQueue()
        catalog = Fake_LabCatalog([self.lab])
        idem = In_Memory_IdempotencyStore()
        ids = Sequential_IdGenerator("ses")
        end = EndSessionUseCase(sessions=self.sessions, queue=queue, clock=self.clock)
        start = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=queue,
            idempotency=idem,
            clock=self.clock,
            ids=ids,
        )

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            if self.on_sleep is not None:
                self.on_sleep()

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
                sessions=self.sessions, catalog=catalog, mastery=self.mastery, clock=self.clock
            ),
            hint_text=lambda *_: None,
            cursor_codec=CursorCodec(self.clock),
            clock=self.clock,
            request_grading=RequestGradingUseCase(
                sessions=self.sessions,
                runs=runs,
                queue=queue,
                idempotency=idem,
                clock=self.clock,
                ids=Sequential_IdGenerator("run"),
            ),
            runs=runs,
            feedback_messages=lambda _lab, _loc: {"fb.ok": "Well done"},
            sleep=sleep,
        )
        self.app = build_version_app(1, [build_labs_router(services)])
        install_error_handlers(self.app)
        self.job = GradeRun(
            sessions=self.sessions,
            runs=runs,
            catalog=catalog,
            sandbox=self.sandbox,
            record=RecordCheckResult(runs=runs, mastery=self.mastery, clock=self.clock),
            clock=self.clock,
            bundle_root=tmp_path,
            expected_loader=lambda *_: {},
        )

    def api(self, method: str, path: str, *, key: str | None = None) -> tuple[int, Any]:
        headers = {"x-user": "u1"}
        if key is not None:
            headers["idempotency-key"] = key
        body = {"lab_id": "lab1"} if path == "/lab-sessions" else None
        result: tuple[int, Any] = _call(self.app, method, path, body=body, headers=headers)
        return result

    def request_run(self) -> tuple[str, str]:
        """Start a ready session with a sandbox and accept a grading run (session -> grading)."""
        status, data = self.api("POST", "/lab-sessions", key="k1")
        assert status == 201, data
        sid = data["stable_id"]
        handle = self.sandbox.provision(build_sandbox_spec(self.lab, sid))
        self.sandbox.add_process(handle, "learner", "bash")
        assert self.sessions.transition(
            sid,
            expected={SessionStatus.PROVISIONING},
            to=SessionStatus.READY,
            sandbox_ref=handle.sandbox_id,
        )
        status, run = self.api("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")
        assert status == 202, run
        return sid, run["stable_id"]

    def stream(self, sid: str, rid: str, last_event_id: str | None = None) -> bytes:
        headers = {"x-user": "u1"}
        if last_event_id is not None:
            headers["last-event-id"] = last_event_id
        status, hdrs, body = _raw(self, f"/lab-sessions/{sid}/grading-runs/{rid}/events", headers)
        assert status == 200
        assert hdrs["content-type"].startswith("text/event-stream")
        return body  # type: ignore[no-any-return]

    def result(self, rid: str, check_id: str, outcome: CheckOutcome) -> None:
        passed = outcome is CheckOutcome.PASSED
        self.runs.append_result(
            rid,
            CheckResult(
                check_id, "t1", outcome, "code", "fb.ok" if passed else f"lab.feedback.{check_id}"
            ),
        )

    def finish(self, rid: str, status: RunStatus) -> None:
        assert self.runs.finish(rid, status=status, graded_at=self.clock.now())


def _shape(body: bytes) -> list[tuple[str, str]]:
    return [(i, e) for i, e, _ in _events(body)]


# --- ordering -----------------------------------------------------------------


def test_results_arriving_across_polls_precede_the_final_run_event(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    steps: list[Callable[[], None]] = [
        lambda: env.result(rid, "c1", CheckOutcome.PASSED),
        lambda: None,  # a poll with nothing new emits nothing
        lambda: env.result(rid, "c2", CheckOutcome.FAILED),
        lambda: env.finish(rid, RunStatus.FAILED),
    ]

    def advance() -> None:
        steps.pop(0)()

    env.on_sleep = advance
    events = _events(env.stream(sid, rid))
    assert [(i, e) for i, e, _ in events] == [("1", "result"), ("2", "result"), ("3", "run")]
    assert [d["check_id"] for _, e, d in events if e == "result"] == ["c1", "c2"]
    assert events[0][2]["message"] == "Well done"
    assert events[2][2]["status"] == "failed"
    assert env.sleeps == [_INTERVAL] * 4  # four polls while the run was still running
    assert steps == []


def test_results_and_completion_in_one_poll_still_order_results_first(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()

    def burst() -> None:
        env.result(rid, "c1", CheckOutcome.PASSED)
        env.result(rid, "c2", CheckOutcome.PASSED)
        env.finish(rid, RunStatus.PASSED)

    env.on_sleep = burst
    assert _shape(env.stream(sid, rid)) == [("1", "result"), ("2", "result"), ("3", "run")]


def test_run_with_no_results_ends_with_only_the_run_event(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.on_sleep = lambda: env.finish(rid, RunStatus.COULD_NOT_COMPLETE)
    events = _events(env.stream(sid, rid))
    assert [(i, e) for i, e, _ in events] == [("1", "run")]
    assert events[0][2]["status"] == "could-not-complete"


def test_stream_never_ends_before_run_is_final(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.result(rid, "c1", CheckOutcome.PASSED)
    polls = 0

    def tick() -> None:
        nonlocal polls
        polls += 1
        if polls == 25:
            env.finish(rid, RunStatus.PASSED)

    env.on_sleep = tick
    assert _shape(env.stream(sid, rid)) == [("1", "result"), ("2", "run")]
    assert polls == 25


# --- resume -------------------------------------------------------------------


def test_resume_from_every_last_event_id_replays_exactly_the_remainder(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.result(rid, "c1", CheckOutcome.PASSED)
    env.result(rid, "c2", CheckOutcome.FAILED)
    env.finish(rid, RunStatus.FAILED)
    full = _events(env.stream(sid, rid))
    assert [i for i, _, _ in full] == ["1", "2", "3"]
    for last in range(0, 6):  # before the start, at each event, and past the end
        resumed = _events(env.stream(sid, rid, str(last)))
        assert resumed == [e for e in full if int(e[0]) > last], last
    for junk in ("abc", "", "-4", "1.5"):
        assert _events(env.stream(sid, rid, junk)) == full, junk


def test_resume_while_running_continues_without_duplicates(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.result(rid, "c1", CheckOutcome.PASSED)
    steps: list[Callable[[], None]] = [
        lambda: env.result(rid, "c2", CheckOutcome.FAILED),
        lambda: env.finish(rid, RunStatus.FAILED),
    ]
    env.on_sleep = lambda: steps.pop(0)()
    assert _shape(env.stream(sid, rid, "1")) == [("2", "result"), ("3", "run")]


def test_resume_after_run_event_on_a_running_to_done_run_sends_nothing(tmp_path: Path) -> None:
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.result(rid, "c1", CheckOutcome.PASSED)
    env.on_sleep = lambda: env.finish(rid, RunStatus.PASSED)
    assert env.stream(sid, rid, "2") == b""


# --- no raw Check output ------------------------------------------------------------


def _leaky_handler(kind: str) -> Callable[..., ExecResult | None]:
    def handler(_sb: Any, command: list[str], _user: str) -> ExecResult | None:
        is_check = command[0] == "/bin/sh" or command[:2] == ["/usr/bin/python3", "-I"]
        if not is_check:
            return None
        exit_code = 0 if command[-1].endswith("c1.sh") or command[-1] == "c1" else 1
        noisy = f"{MARKER} stdout {json.dumps({'cases': {}, 'note': MARKER})}\n"
        return ExecResult(exit_code, noisy.encode(), f"{MARKER} stderr\n".encode(), False)

    return handler


@pytest.mark.parametrize("kind", ["terminal", "code"])
def test_planted_check_output_is_absent_from_every_response_and_stream(
    tmp_path: Path, kind: str
) -> None:
    env = _Env(tmp_path, kind)
    sid, rid = env.request_run()
    env.sandbox.exec_handler = _leaky_handler(kind)
    status = env.job.execute(rid)
    assert status is not None
    stored = env.runs.get(rid)
    assert stored is not None and len(stored.results) == 2  # the Checks really ran

    payloads: list[bytes] = []
    _, posted = env.api("POST", f"/lab-sessions/{sid}/grading-runs", key="g1")  # replay
    payloads.append(json.dumps(posted).encode())
    _, fetched = env.api("GET", f"/lab-sessions/{sid}/grading-runs/{rid}")
    payloads.append(json.dumps(fetched).encode())
    _, session = env.api("GET", f"/lab-sessions/{sid}")
    payloads.append(json.dumps(session).encode())
    full = env.stream(sid, rid)
    payloads.append(full)
    for last in range(0, 4):
        payloads.append(env.stream(sid, rid, str(last)))

    assert _events(full)[-1][1] == "run"
    for payload in payloads:
        assert MARKER.encode() not in payload
        assert b"outcome_code" not in payload
    # Check output is not persisted on the stored results either.
    assert MARKER not in repr(stored)


def test_marker_in_every_stored_field_but_outcome_code_is_not_the_leak_path(
    tmp_path: Path,
) -> None:
    """A result built from checks whose output carried the marker exposes only the whitelist."""
    env = _Env(tmp_path)
    sid, rid = env.request_run()
    env.sandbox.exec_handler = _leaky_handler("terminal")
    env.job.execute(rid)
    _, fetched = env.api("GET", f"/lab-sessions/{sid}/grading-runs/{rid}")
    for r in fetched["results"]:
        assert set(r) == {"task_id", "check_id", "outcome", "feedback_key", "message"}
    events = _events(env.stream(sid, rid))
    for _, kind, data in events:
        if kind == "result":
            assert set(data) == {"task_id", "check_id", "outcome", "feedback_key", "message"}
        else:
            assert set(data) == {"stable_id", "session_id", "status", "graded_at"}
