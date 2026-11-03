"""Property tests over the mounted Served_App (api-route-wiring task 8.5).

Every property drives the real routers, use cases, middleware, and error handlers through
HTTP over the in-memory fakes. Each example uses its own learner, so examples never depend on
one another. Hypothesis runs at least 100 examples per property.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from edu_it_api.route_table import ROUTE_TABLE
from edu_it_domain.config import MIN_PROJECT_EVIDENCE_COUNT
from edu_it_domain.learning.models import ProjectSubmissionRef, Rubric, RubricCriterion
from edu_it_infrastructure.project_reviewer import Pending_ProjectReviewer
from hypothesis import given, settings
from hypothesis import strategies as st


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
TRACK: str = _H.TRACK
QUIZ: str = _H.QUIZ
PROJECT: str = _H.PROJECT
SESSION_COOKIE: str = _H.SESSION_COOKIE
CSRF_COOKIE: str = _H.CSRF_COOKIE

_EXAMPLES = settings(max_examples=100, deadline=None)

_CONCEPT = "concept.changing-permissions"
_KEY = {"idempotency-key": "property-key"}


def _footprint(served: Any) -> tuple[int, ...]:
    """Sizes of every in-memory collection a request could change."""
    adapters = served.adapters
    sizes: list[int] = []
    for repo in (
        adapters.users,
        adapters.sessions,
        adapters.credentials,
        adapters.verification_links,
        adapters.enrollments,
        adapters.attempts,
        adapters.mastery,
        adapters.reviews,
        adapters.lab_sessions,
        adapters.grading_runs,
        adapters.mailer,
    ):
        sizes.extend(
            len(value) for value in vars(repo).values() if isinstance(value, dict | list | set)
        )
    return tuple(sizes)


def _requests(served: Any) -> dict[tuple[str, str], dict[str, Any]]:
    """A well-formed request for every HTTP route of the table, keyed by (method, path)."""
    full = {c.stable_id: c.max_points for c in served.criteria}
    return {
        ("GET", "/auth/session"): {},
        ("POST", "/account/export"): {"body": {}},
        ("GET", "/account/export/download"): {},
        ("POST", "/account/deletion"): {"body": {"confirm": False}},
        ("POST", "/enrollments"): {"body": {"track_id": TRACK}},
        ("GET", "/enrollments"): {},
        ("GET", "/progress"): {"query": f"track_id={TRACK}"},
        ("GET", "/lessons/{stable_id}"): {},
        ("POST", "/quiz-attempts"): {
            "body": {"quiz_id": QUIZ, "selections": {}},
            "headers": _KEY,
        },
        ("GET", "/quiz-attempts"): {"query": f"quiz_id={QUIZ}"},
        ("POST", "/hints"): {"body": {"quiz_id": QUIZ, "question_id": "q"}},
        ("GET", "/reviews"): {"query": f"concept_id={_CONCEPT}"},
        ("POST", "/reviews"): {"body": {"concept_id": _CONCEPT, "selections": {}}},
        ("POST", "/project-submissions"): {
            "body": {
                "project_id": PROJECT,
                "self_assessment": full,
                "evidence": ["https://example.test/a", "https://example.test/b"],
            }
        },
        ("GET", "/quizzes/{stable_id}"): {},
        ("GET", "/projects/{stable_id}"): {},
        ("GET", "/labs/{lab_id}"): {},
        ("POST", "/lab-sessions"): {"body": {"lab_id": "x"}, "headers": _KEY},
        ("GET", "/lab-sessions"): {},
        ("GET", "/lab-sessions/{session_id}"): {},
        ("DELETE", "/lab-sessions/{session_id}"): {},
        ("POST", "/lab-sessions/{session_id}/reset"): {"body": {}, "headers": _KEY},
        ("PUT", "/lab-sessions/{session_id}/files"): {"body": {"files": {}}},
        ("POST", "/lab-sessions/{session_id}/hints"): {"body": {"task_id": "t", "level": 1}},
        ("POST", "/lab-sessions/{session_id}/grading-runs"): {"body": {}, "headers": _KEY},
        ("GET", "/lab-sessions/{session_id}/grading-runs/{run_id}"): {},
        ("GET", "/lab-sessions/{session_id}/grading-runs/{run_id}/events"): {},
        ("GET", "/review-tasks"): {},
        ("GET", "/review-tasks/{task_id}"): {},
        ("POST", "/review-tasks/{task_id}/approve"): {"body": {}},
        ("POST", "/review-tasks/{task_id}/reject"): {"body": {}},
        ("POST", "/review-tasks/{task_id}/request-changes"): {"body": {"comment": "fix"}},
        ("POST", "/review-tasks/{task_id}/edit"): {"body": {"body": {"prompt": {"en": "x"}}}},
        ("POST", "/problem-reports"): {
            "body": {
                "item_kind": "lesson",
                "item_id": "l",
                "content_version": "v1",
                "category": "unclear",
            },
            "headers": _KEY,
        },
        ("GET", "/path-recommendation"): {},
        ("POST", "/path-recommendation/{recommendation_id}/dismissal"): {"body": {}},
        # Public routes that are still unsafe, for the CSRF property.
        ("POST", "/auth/sign-up"): {
            "body": {"email": "csrf@example.test", "password": _H.PASSWORD}
        },
        ("POST", "/auth/verify-email"): {"body": {"token": "t"}},
        ("POST", "/auth/verify-email/resend"): {"body": {"email": "csrf@example.test"}},
        ("POST", "/auth/password-reset"): {"body": {"email": "csrf@example.test"}},
        ("POST", "/auth/password-reset/complete"): {
            "body": {"token": "unknown-reset-token", "new_password": "a sufficiently long one"}
        },
        ("POST", "/auth/sign-in"): {
            "body": {"email": "csrf@example.test", "password": _H.PASSWORD}
        },
        ("POST", "/auth/sign-out"): {"body": {}},
    }


def _send(browser: Any, method: str, template: str, spec: dict[str, Any], **kw: Any) -> Any:
    path = "/v1" + re.sub(r"\{[^}]+\}", "x", template)
    return browser.request(
        method,
        path,
        spec.get("body"),
        query=spec.get("query", ""),
        headers=spec.get("headers"),
        **kw,
    )


# Feature: api-route-wiring, Property 5: Authenticated routes reject missing or invalid sessions
def test_property_5_authenticated_routes_reject_missing_or_invalid_sessions(
    shared_served: Any,
) -> None:
    """**Validates: Requirements 1.3, 1.4, 4.7**"""
    served = shared_served
    requests = _requests(served)
    routes = [
        (e.method, e.path) for e in ROUTE_TABLE if e.authentication == "cookie" and e.method != "WS"
    ]
    assert set(routes) <= set(requests), f"add a request for {set(routes) - set(requests)}"

    browser, _ = served.signed_in()
    revoked = browser.jar[SESSION_COOKIE]
    assert browser.post("/v1/auth/sign-out", {}).status == 204

    # Positive control: a live session is never answered with 401 by these requests.
    live, _ = served.signed_in()
    for method, path in routes:
        assert _send(live, method, path, requests[(method, path)]).status != 401, (method, path)

    cookie_values = st.one_of(
        st.none(),
        st.just(revoked),
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=64),
    )

    @_EXAMPLES
    @given(st.sampled_from(routes), cookie_values)
    def check(route: tuple[str, str], cookie: str | None) -> None:
        method, path = route
        client = served.browser()
        if cookie is not None:
            client.jar[SESSION_COOKIE] = cookie
        before = _footprint(served)
        reply = _send(client, method, path, requests[route])
        assert reply.status == 401, (route, cookie, reply.status)
        assert reply.is_problem
        assert _footprint(served) == before

    check()


# Feature: api-route-wiring, Property 7: CSRF issuance and enforcement
def test_property_7_csrf_issuance_and_enforcement(shared_served: Any) -> None:
    """**Validates: Requirements 2.4, 2.5, 2.6**"""
    served = shared_served
    requests = _requests(served)
    unsafe = [
        (e.method, e.path)
        for e in ROUTE_TABLE
        if e.method in {"POST", "PUT", "DELETE"} and e.condition == "always"
    ]
    assert set(unsafe) <= set(requests), f"add a request for {set(unsafe) - set(requests)}"
    url_safe = re.compile(r"^[A-Za-z0-9_-]{32,}$")

    @_EXAMPLES
    @given(st.integers(min_value=1, max_value=4))
    def issuance(calls: int) -> None:
        browser = served.browser()
        tokens = []
        for _ in range(calls):
            reply = browser.get("/v1/auth/csrf")
            assert reply.status == 200
            tokens.append(reply.json()["token"])
        assert len(set(tokens)) == 1
        assert url_safe.match(tokens[0])
        assert browser.jar[CSRF_COOKIE] == tokens[0]
        # The issued pair passes the middleware on an unsafe route.
        assert (
            browser.post(
                "/v1/auth/sign-out", {}, csrf=False, headers={"x-csrf-token": tokens[0]}
            ).status
            == 204
        )

    issuance()

    token_alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-_"

    @_EXAMPLES
    @given(st.text(alphabet=token_alphabet, min_size=16, max_size=64))
    def echo(existing: str) -> None:
        browser = served.browser()
        browser.jar[CSRF_COOKIE] = existing
        reply = browser.get("/v1/auth/csrf")
        assert reply.json()["token"] == existing
        assert "set-cookie" not in {k.lower() for k, _ in reply.headers}

    echo()

    pairs = st.one_of(
        st.tuples(st.none(), st.none()),
        st.tuples(st.text(alphabet=token_alphabet, min_size=16, max_size=40), st.none()),
        st.tuples(st.none(), st.text(alphabet=token_alphabet, min_size=16, max_size=40)),
        st.tuples(
            st.text(alphabet=token_alphabet, min_size=16, max_size=40),
            st.text(alphabet=token_alphabet, min_size=16, max_size=40),
        ).filter(lambda p: p[0] != p[1]),
        st.tuples(st.just("short"), st.just("short")),
    )

    @_EXAMPLES
    @given(st.sampled_from(unsafe), pairs)
    def enforcement(route: tuple[str, str], pair: tuple[str | None, str | None]) -> None:
        header, cookie = pair
        client = served.browser()
        if cookie is not None:
            client.jar[CSRF_COOKIE] = cookie
        extra = {} if header is None else {"x-csrf-token": header}
        spec = dict(requests[route])
        spec["headers"] = {**spec.get("headers", {}), **extra}
        before = _footprint(served)
        reply = _send(client, route[0], route[1], spec, csrf=False)
        assert reply.status == 403, (route, pair, reply.status)
        assert reply.is_problem
        assert _footprint(served) == before

    enforcement()


# Feature: api-route-wiring, Property 15: Repeated mutations are idempotent
def test_property_15_repeated_mutations_are_idempotent(shared_served: Any) -> None:
    """**Validates: Requirements 4.5, 9.4, 9.6**

    A replayed quiz attempt differs from the first response only in its ``replayed`` flag,
    which is the documented signal that the stored result was returned.
    """
    served = shared_served

    @_EXAMPLES
    @given(st.integers(min_value=1, max_value=5), st.booleans())
    def sign_out(calls: int, authenticated: bool) -> None:
        browser = served.signed_in()[0] if authenticated else served.browser()
        assert all(browser.post("/v1/auth/sign-out", {}).status == 204 for _ in range(calls))

    sign_out()

    @_EXAMPLES
    @given(st.integers(min_value=1, max_value=5))
    def enroll(calls: int) -> None:
        browser, _ = served.signed_in()
        statuses = [
            browser.post("/v1/enrollments", {"track_id": TRACK}).status for _ in range(calls)
        ]
        assert statuses == [201] + [200] * (calls - 1)
        user = served.user_id(browser)
        assert len(served.adapters.enrollments.list_for_user(user)) == 1

    enroll()

    @_EXAMPLES
    @given(st.integers(min_value=1, max_value=5), st.data())
    def attempt(calls: int, data: st.DataObject) -> None:
        browser, _ = served.signed_in()
        selections = {
            q.stable_id: data.draw(st.sampled_from([o.option_id for o in q.options]))
            for q in served.questions
        }
        body = {"quiz_id": QUIZ, "selections": selections}
        key = {"idempotency-key": data.draw(st.uuids().map(str))}
        user = served.user_id(browser)
        replies = [browser.post("/v1/quiz-attempts", body, headers=key) for _ in range(calls)]
        assert all(r.status == 200 for r in replies)
        assert [r.json()["replayed"] for r in replies] == [False] + [True] * (calls - 1)
        stripped = [{k: v for k, v in r.json().items() if k != "replayed"} for r in replies]
        assert all(s == stripped[0] for s in stripped)
        recorded = served.adapters.attempts.list_quiz_attempts(user, QUIZ)
        assert len(recorded) == 1

    attempt()


# Feature: api-route-wiring, Property 16: Project submissions are pending or rejected whole
def test_property_16_project_submissions_are_pending_or_rejected_whole(
    shared_served: Any,
) -> None:
    """**Validates: Requirements 11.1, 11.3, 11.4**"""
    served = shared_served
    criteria = {c.stable_id: c.max_points for c in served.criteria}

    @st.composite
    def submissions(draw: st.DrawFn) -> tuple[dict[str, int], list[str]]:
        assessed: dict[str, int] = {}
        for criterion, maximum in criteria.items():
            if draw(st.integers(0, 9)) == 0:
                continue  # leave the criterion out
            assessed[criterion] = draw(st.integers(min_value=-3, max_value=maximum + 3))
        if draw(st.integers(0, 9)) == 0:
            assessed["rubric_criterion.unknown"] = draw(st.integers(0, 5))
        evidence = draw(
            st.lists(st.sampled_from(["", "  ", "https://e.test/a", "proof"]), max_size=4)
        )
        return assessed, evidence

    def is_complete(assessed: dict[str, int], evidence: list[str]) -> bool:
        return (
            assessed.keys() == criteria.keys()
            and all(0 <= points <= criteria[c] for c, points in assessed.items())
            and sum(1 for item in evidence if item.strip()) >= MIN_PROJECT_EVIDENCE_COUNT
        )

    @_EXAMPLES
    @given(submissions())
    def check(submission: tuple[dict[str, int], list[str]]) -> None:
        assessed, evidence = submission
        browser, _ = served.signed_in()
        before = len(served.adapters.attempts._submissions)
        reply = browser.post(
            "/v1/project-submissions",
            {"project_id": PROJECT, "self_assessment": assessed, "evidence": evidence},
        )
        recorded = len(served.adapters.attempts._submissions) - before
        if is_complete(assessed, evidence):
            assert reply.status == 201, reply.json()
            assert reply.json()["status"] == "pending"
            assert reply.json()["review_outcome"] is None
            assert recorded == 1
        else:
            assert reply.is_problem and 400 <= reply.status < 500, reply.status
            assert recorded == 0

    check()

    @_EXAMPLES
    @given(st.text(min_size=1, max_size=12), st.integers(min_value=1, max_value=6))
    def pending_reviewer_returns_none(project_id: str, criteria_count: int) -> None:
        rubric = Rubric(
            stable_id="rubric.any",
            project_id=project_id,
            criteria=tuple(RubricCriterion(f"c{i}", max_points=5) for i in range(criteria_count)),
        )
        assert (
            Pending_ProjectReviewer().review(ProjectSubmissionRef("s1", project_id), rubric) is None
        )

    pending_reviewer_returns_none()


# Feature: api-route-wiring, Property 17: Learning responses expose stable identifiers only
def test_property_17_learning_responses_expose_stable_identifiers_only(shared_served: Any) -> None:
    """**Validates: Requirements 9.8**

    ``option_id`` is the per-question choice number the quiz contract defines, not a primary
    key, so it is the one integer identifier allowed.
    """
    served = shared_served

    def violations(node: Any, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                where = f"{path}.{key}"
                if key == "id":
                    found.append(f"{where}: bare id member")
                elif key.endswith("_id") and key != "option_id" and isinstance(value, int | float):
                    found.append(f"{where}: integer reference")
                elif key.endswith("ids") and isinstance(value, list):
                    found.extend(f"{where}: integer reference" for v in value if isinstance(v, int))
                found.extend(violations(value, where))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(violations(value, f"{path}[{index}]"))
        return found

    @_EXAMPLES
    @given(st.sampled_from(["fr", "en"]), st.booleans(), st.data())
    def check(locale: str, with_activity: bool, data: st.DataObject) -> None:
        browser, _ = served.signed_in()
        assert browser.post("/v1/enrollments", {"track_id": TRACK}, locale=locale).status == 201
        if with_activity:
            selections = {
                q.stable_id: data.draw(st.sampled_from([o.option_id for o in q.options]))
                for q in served.questions
            }
            browser.post(
                "/v1/quiz-attempts",
                {"quiz_id": QUIZ, "selections": selections},
                headers={"idempotency-key": data.draw(st.uuids().map(str))},
            )
            browser.post(
                "/v1/hints", {"quiz_id": QUIZ, "question_id": served.questions[0].stable_id}
            )
        responses = [
            browser.get("/v1/enrollments"),
            browser.get("/v1/progress", query=f"track_id={TRACK}"),
            browser.get("/v1/quiz-attempts", query=f"quiz_id={QUIZ}"),
            browser.get(f"/v1/quizzes/{QUIZ}", query=f"locale={locale}"),
            browser.get(f"/v1/projects/{PROJECT}", query=f"locale={locale}"),
        ]
        for reply in responses:
            assert reply.status == 200, reply.json()
            assert violations(reply.json()) == []

    check()
