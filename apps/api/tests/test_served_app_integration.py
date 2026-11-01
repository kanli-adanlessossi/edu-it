"""Integration tests for the mounted Served_App (api-route-wiring task 8.6).

Everything runs in process over the in-memory fakes; no test opens a socket.

**Validates: Requirements 1.5, 1.6, 1.7, 1.8, 8.6, 9.9, 15.2, 15.3, 16.1, 16.2, 17.7**
"""

from __future__ import annotations

import importlib.util
import json
import logging
import socket
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from edu_it_api.app import create_app
from edu_it_api.composition import build_adapters
from edu_it_api.openapi_export import main as export_main
from edu_it_api.openapi_export import openapi_document_for_major
from edu_it_api.route_table import ROUTE_TABLE
from edu_it_api.serve import build_served_app, compose_served_app, load_settings
from edu_it_api.startup import StartupRefused
from edu_it_application.labs import RecordCheckResult
from edu_it_content.lab_runtime import load_synced_store
from edu_it_domain.labs.models import (
    CheckOutcome,
    CheckResult,
    GradingRun,
    LabSession,
    RunStatus,
    SessionStatus,
)
from edu_it_domain.ports.health import DependencyProbe
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    SandboxBackend,
)


def _load_harness() -> Any:
    name = "served_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_H = _load_harness()
Browser = _H.Browser
CONTENT_ROOT: Path = _H.CONTENT_ROOT
PROJECT: str = _H.PROJECT
SESSION_COOKIE: str = _H.SESSION_COOKIE
TRACK: str = _H.TRACK

_PROBES = [
    DependencyProbe(name="postgresql", is_reachable=lambda: True),
    DependencyProbe(name="redis", is_reachable=lambda: False),
]


def _doc_operations(document: dict[str, Any]) -> set[tuple[str, str]]:
    methods = {"get", "post", "put", "delete", "patch"}
    return {
        (method.upper(), path)
        for path, item in document["paths"].items()
        for method in item
        if method in methods
    }


# -- OpenAPI and the route table ----------------------------------------------------------


def test_openapi_paths_and_methods_equal_the_route_table() -> None:
    document = openapi_document_for_major(1)
    expected = {(e.method, e.path) for e in ROUTE_TABLE if e.method != "WS"}
    assert _doc_operations(document) == expected


def test_running_served_app_publishes_the_same_document(served: Any) -> None:
    reply = served.browser().get("/v1/openapi.json")
    assert reply.status == 200
    # The default served app has no GitHub variables, so conditional routes are not mounted.
    expected = {
        (e.method, e.path) for e in ROUTE_TABLE if e.method != "WS" and e.condition == "always"
    }
    assert _doc_operations(reply.json()) == expected


def test_lab_entries_of_the_route_table_equal_the_real_lab_router_routes(served: Any) -> None:
    actual: set[tuple[str, str]] = set()
    for route in served.composed.routers.lab_router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if methods is None:  # a WebSocket route
            actual.add(("WS", path))
        else:
            actual.update((m, path) for m in methods if m not in {"HEAD", "OPTIONS"})
    expected = {(e.method, e.path) for e in ROUTE_TABLE if e.use_case == "existing"}
    assert actual == expected


def test_export_runs_with_sockets_forbidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("the export must not open a connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    out = tmp_path / "v1.json"
    assert export_main(["--major", "1", "--out", str(out)]) == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert _doc_operations(document) == {
        (e.method, e.path) for e in ROUTE_TABLE if e.method != "WS"
    }


def test_fake_adapters_build_no_datastore_client() -> None:
    adapters = build_adapters(CompositionSettings(), fakes=True)
    assert adapters.redis is None
    assert adapters.fakes is True


def test_generated_client_document_lists_every_route_table_entry() -> None:
    exported = Path(__file__).resolve().parents[2] / "web" / "openapi" / "v1.json"
    if not exported.is_file():
        pytest.skip("apps/web/openapi/v1.json is generated and not checked in")
    document = json.loads(exported.read_text(encoding="utf-8"))
    assert _doc_operations(document) == {
        (e.method, e.path) for e in ROUTE_TABLE if e.method != "WS"
    }


# -- /health and /ready --------------------------------------------------------------------


def test_health_and_ready_are_unchanged_by_the_mounted_routers() -> None:
    bare = create_app(lambda: _PROBES)
    served = compose_served_app(
        CompositionSettings(),
        fakes=True,
        content_root=CONTENT_ROOT,
        probes_provider=lambda: _PROBES,
    )
    for path in ("/health", "/ready"):
        before = Browser(bare).get(path)
        after = Browser(served.app).get(path)
        assert (before.status, before.json()) == (after.status, after.json())
    assert Browser(served.app).get("/health").status == 200
    assert Browser(served.app).get("/ready").status == 503


# -- Startup refusal -----------------------------------------------------------------------


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def startup_log() -> Any:
    handler = _Records()
    logger = logging.getLogger("edu_it_api.startup")
    logger.addHandler(handler)
    yield handler.records
    logger.removeHandler(handler)


@pytest.mark.parametrize("bad", ["not-a-url", "ftp://web.test", "/relative", "http://"])
def test_create_app_refuses_a_bad_web_base_url(bad: str, startup_log: list[Any]) -> None:
    with pytest.raises(StartupRefused) as raised:
        create_app(web_base_url=bad)
    assert raised.value.component == "WEB_BASE_URL"
    assert [r.adapter for r in startup_log] == ["WEB_BASE_URL"]
    assert bad not in " ".join(r.getMessage() for r in startup_log)


def test_served_app_refuses_a_bad_web_base_url_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, startup_log: list[Any]
) -> None:
    monkeypatch.setenv("WEB_BASE_URL", "ftp://secret-host.test")
    with pytest.raises(StartupRefused) as raised:
        load_settings()
    assert raised.value.component == "WEB_BASE_URL"
    with pytest.raises(StartupRefused):
        build_served_app()
    assert "secret-host" not in " ".join(r.getMessage() for r in startup_log)


def test_startup_is_refused_when_a_required_adapter_cannot_be_built(
    startup_log: list[Any],
) -> None:
    settings = CompositionSettings(
        environment=Environment.PRODUCTION,
        sandbox_provider=SandboxBackend.DOCKER,
        database_url="postgresql://user:s3cr3t-pw@db.test:5432/edu_it",
        redis_url="redis://:s3cr3t-pw@cache.test:6379/0",
    )
    with pytest.raises(StartupRefused) as raised:
        build_adapters(settings, fakes=False)
    assert raised.value.component == "sandbox_provider"
    assert [r.adapter for r in startup_log] == ["sandbox_provider"]
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in startup_log)
    assert "s3cr3t-pw" not in logged


def test_real_adapters_build_without_contacting_a_datastore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("building the adapters must not open a connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    adapters = build_adapters(CompositionSettings(), fakes=False)
    assert adapters.fakes is False
    assert type(adapters.clock).__name__ == "System_Clock"
    assert type(adapters.project_reviewer).__name__ == "Pending_ProjectReviewer"


# -- In-process HTTP journey ---------------------------------------------------------------


def test_http_journey_from_sign_up_to_sign_out(served: Any) -> None:
    browser = served.browser()
    email = served.new_email()

    assert served.sign_up(browser, email).status == 202
    mail = served.adapters.mailer.sent_to(email)[-1]
    assert "/verify-email?token=" in mail.body
    assert served.verify(browser, email).status == 200

    signed_in = served.sign_in(browser, email)
    assert signed_in.status == 200
    assert signed_in.json() == {"redirect_to": "/dashboard"}
    assert SESSION_COOKIE in browser.jar
    user_id = served.user_id(browser)

    enrolled = browser.post("/v1/enrollments", {"track_id": TRACK})
    assert enrolled.status == 201
    assert browser.post("/v1/enrollments", {"track_id": TRACK}).status == 200

    selections = {
        q.stable_id: next(o.option_id for o in q.options if o.is_correct) for q in served.questions
    }
    attempt = browser.post(
        "/v1/quiz-attempts",
        {"quiz_id": "quiz.changing-permissions", "selections": selections},
        headers={"idempotency-key": "journey-1"},
    )
    assert attempt.status == 200, attempt.json()
    assert attempt.json()["attempt"]["score"] == 100

    submission = browser.post(
        "/v1/project-submissions",
        {
            "project_id": PROJECT,
            "self_assessment": {c.stable_id: c.max_points for c in served.criteria},
            "evidence": ["https://example.test/a", "https://example.test/b"],
        },
    )
    assert submission.status == 201, submission.json()
    assert submission.json()["status"] == "pending"

    assert browser.post("/v1/auth/sign-out", {}).status == 204
    assert browser.get("/v1/auth/session").status == 401
    assert user_id


def test_a_minted_learner_cookie_authenticates_against_the_served_app(
    served: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run ``tools/perf/stack.mint_learner`` with its datastores replaced by the fakes."""
    path = Path(__file__).resolve().parents[3] / "tools" / "perf" / "stack.py"
    spec = importlib.util.spec_from_file_location("perf_stack_under_test", path)
    assert spec is not None and spec.loader is not None
    stack = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stack
    spec.loader.exec_module(stack)

    import edu_it_infrastructure.adapters.redis_support as redis_support
    import edu_it_infrastructure.adapters.session_store as session_store_module
    import edu_it_infrastructure.persistence.repositories as repositories

    adapters = served.adapters
    monkeypatch.setattr(redis_support, "redis_from_url", lambda _url: object())
    monkeypatch.setattr(
        session_store_module, "Redis_SessionStore", lambda _c, _clock: adapters.session_store
    )
    monkeypatch.setattr(repositories, "Postgres_UserRepository", lambda _url: adapters.users)
    # The fake session store lives on the Test_Clock; expiry must be later than its "now".
    learner = stack.mint_learner()

    browser = served.browser()
    browser.jar[SESSION_COOKIE] = learner.ref
    reply = browser.get("/v1/auth/session")
    assert reply.status == 200
    assert reply.json() == {"user_id": learner.user_id}
    assert browser.get("/v1/progress", query=f"track_id={TRACK}").status != 401


# -- Progress after the worker records a passed Check -------------------------------------


def test_progress_shows_completion_after_the_worker_records_passed_checks(served: Any) -> None:
    browser, _ = served.signed_in()
    user_id = served.user_id(browser)
    assert browser.post("/v1/enrollments", {"track_id": TRACK}).status == 201

    adapters = served.adapters
    store, _report = load_synced_store(CONTENT_ROOT)
    lab = Content_LabCatalog(store).get("lab.fix-the-shared-folder")
    assert lab is not None
    now = adapters.clock.now()
    session = LabSession(
        "lab-s1", user_id, lab.lab_id, SessionStatus.GRADING, now, now + timedelta(minutes=30)
    )
    adapters.lab_sessions.add_if_no_active(session)
    adapters.grading_runs.add(
        GradingRun("run-1", "lab-s1", RunStatus.RUNNING, now, now + timedelta(seconds=15))
    )

    before = browser.get("/v1/progress", query=f"track_id={TRACK}").json()
    assert all(not item["passed"] for item in before["labs"])

    # The Worker_Service applies the same use case to the same repositories (Req 15.2, 15.3).
    record = RecordCheckResult(
        runs=adapters.grading_runs, mastery=adapters.mastery, clock=adapters.clock
    )
    for check in lab.checks:
        record.execute(
            "run-1",
            session,
            lab,
            CheckResult(
                check.check_id, check.task_id, CheckOutcome.PASSED, "pass", check.outcomes["pass"]
            ),
        )

    after = browser.get("/v1/progress", query=f"track_id={TRACK}")
    assert after.status == 200
    entry = next(item for item in after.json()["labs"] if item["lab_id"] == lab.lab_id)
    assert entry["passed"] is True
    assert set(entry["completed_task_ids"]) == {t.task_id for t in lab.tasks}
