"""Property test: lab responses expose no internal key (task 7.8, Property 37).

Drives the real mounted application (``create_app(lab_router=...)`` under ``/v1``,
with the CSRF guard, request logging and error handlers in place) over the real
router, use cases and domain rules on the in-memory infrastructure fakes, using a
minimal in-process ASGI call (the project pins no HTTP client).

The domain objects carry no numeric row key, so the test plants "internal" numbers
in every place a careless serializer could leak one from: the sandbox reference,
the provisioning-attempt and terminal-epoch counters, a Check's internal outcome
code, and the messages of exceptions raised by a repository and by the sandbox
provider. They are drawn from a range far above every legitimate integer (counts,
hint levels, limits) so a hit can only be a leak.

**Validates: Requirements 17.1, 17.6**
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from collections.abc import MutableMapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from edu_it_api.app import create_app
from edu_it_domain.labs.models import CheckOutcome, CheckResult, RunStatus
from edu_it_domain.ports.sandbox import ProviderUnavailable
from fastapi import FastAPI
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_SPEC = importlib.util.spec_from_file_location(
    "_labs_routes_env_p37", Path(__file__).with_name("test_labs_routes.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_Env: Any = _MODULE._Env

_CSRF = "c" * 32
_KEY = st.integers(min_value=10**12, max_value=10**15)


def _http(
    app: FastAPI,
    method: str,
    path: str,
    *,
    body: Any = None,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """One in-process request through the root app; returns status, headers, raw body."""
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = {"content-type": "application/json", "x-user": "u1"}
    if method not in ("GET", "HEAD"):
        hdrs |= {"x-csrf-token": _CSRF, "cookie": f"csrf_token={_CSRF}"}
    hdrs |= {k.lower(): v for k, v in (headers or {}).items()}
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def run() -> None:
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.Event().wait()  # the client stays connected
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        try:
            await app(scope, receive, send)
        except Exception:
            # ServerErrorMiddleware re-raises after it has sent the generic 500 problem.
            if not any(m["type"] == "http.response.start" for m in sent):
                raise

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    out_headers = {k.decode(): v.decode() for k, v in start["headers"]}
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], out_headers, payload


def _walk(value: Any) -> list[tuple[str, Any]]:
    """Every ``(key, leaf)`` pair of a decoded JSON document."""
    if isinstance(value, dict):
        return [
            (k, leaf)
            for k, v in value.items()
            for _, leaf in [(k, v)]
            if not isinstance(v, dict | list)
        ] + [pair for v in value.values() for pair in _walk(v)]
    if isinstance(value, list):
        return [pair for v in value for pair in _walk(v)]
    return []


def _decoded_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()


def _sse_payloads(body: bytes) -> list[Any]:
    out = []
    for block in body.decode().strip().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


class _Exchange:
    """Collects every response so the whole surface is scanned for the planted keys."""

    def __init__(self, app: FastAPI, needles: list[str], known_ids: set[str]) -> None:
        self.app = app
        self.needles = needles
        self.known_ids = known_ids
        self.statuses: list[int] = []

    def call(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        status, headers, raw = _http(self.app, method, path, **kwargs)
        self.statuses.append(status)
        text = raw.decode() + json.dumps(headers) + path
        # The cursor travels in the body (and is opaque); scan what it decodes to as well.
        decoded: Any = None
        if headers.get("content-type", "").startswith("text/event-stream"):
            decoded = _sse_payloads(raw)
        elif raw:
            decoded = json.loads(raw)
        for _name, leaf in _walk(decoded):
            if isinstance(leaf, str) and leaf.startswith("ey") and len(leaf) > 20:
                text += _decoded_cursor(leaf)
        for needle in self.needles:
            assert needle not in text, f"internal key {needle} leaked by {method} {path}"
        # No integer is a row key: ids are strings, and every integer is far below the plant range.
        for name, leaf in _walk(decoded):
            if name == "id" or name.endswith("_id") or name == "stable_id":
                assert isinstance(leaf, str), f"{name} is not a stable_id string"
            if isinstance(leaf, int) and not isinstance(leaf, bool):
                assert leaf < 10**6
        return status, headers, decoded


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    sandbox_key=_KEY,
    attempts_key=_KEY,
    epoch_key=_KEY,
    code_key=_KEY,
    repo_error_key=_KEY,
    provider_error_key=_KEY,
    extra_sessions=st.integers(min_value=0, max_value=3),
    limit=st.integers(min_value=1, max_value=3),
    locale=st.sampled_from(["en", "fr"]),
)
def test_lab_responses_expose_no_internal_key(
    sandbox_key: int,
    attempts_key: int,
    epoch_key: int,
    code_key: int,
    repo_error_key: int,
    provider_error_key: int,
    extra_sessions: int,
    limit: int,
    locale: str,
) -> None:
    # Feature: hands-on-labs, Property 37: Lab responses expose no internal key
    keys = [sandbox_key, attempts_key, epoch_key, code_key, repo_error_key, provider_error_key]
    env = _Env()
    root = create_app(lab_router=env.router)
    lang = {"accept-language": locale}
    ex = _Exchange(root, [str(k) for k in keys], set())

    def plant(session_id: str, *, sandbox: bool = True) -> None:
        stored = env.sessions.get(session_id)
        assert stored is not None
        env.sessions._sessions[session_id] = replace(
            stored,
            sandbox_ref=f"sbx-{sandbox_key}" if sandbox else stored.sandbox_ref,
            provision_attempts=attempts_key,
            terminal_epoch=epoch_key,
        )

    # Earlier, ended sessions give the paginated list several pages with cursors.
    session_ids: list[str] = []
    for i in range(extra_sessions):
        status, _, data = ex.call(
            "POST",
            "/v1/lab-sessions",
            body={"lab_id": "lab1"},
            headers={"idempotency-key": f"e{i}"},
        )
        assert status == 201
        sid = data["stable_id"]
        session_ids.append(sid)
        plant(sid)
        env.clock.advance(timedelta(seconds=1))
        assert ex.call("DELETE", f"/v1/lab-sessions/{sid}")[0] == 200

    status, _, data = ex.call(
        "POST", "/v1/lab-sessions", body={"lab_id": "lab1"}, headers={"idempotency-key": "live"}
    )
    assert status == 201
    sid = data["stable_id"]
    session_ids.append(sid)
    env.make_ready(sid)
    plant(sid, sandbox=False)  # the fake sandbox still needs the real reference
    base = f"/v1/lab-sessions/{sid}"

    # Lab view (success and error), session view, error problems of several kinds.
    assert ex.call("GET", "/v1/labs/lab1", query=f"locale={locale}", headers=lang)[0] == 200
    assert ex.call("GET", "/v1/labs/nope", headers=lang)[0] == 404
    assert ex.call("GET", base, headers=lang)[0] == 200
    assert ex.call("GET", "/v1/lab-sessions/missing", headers=lang)[0] == 404
    assert ex.call("GET", base, headers={"x-user": "u2"})[0] == 404
    assert ex.call("POST", "/v1/lab-sessions", body={"lab_id": "lab1"})[0] == 422
    assert ex.call("GET", "/v1/lab-sessions", query="cursor=garbage")[0] == 400
    status, _, problem = ex.call(
        "POST", "/v1/lab-sessions", body={"lab_id": "lab1"}, headers={"idempotency-key": "again"}
    )
    assert status == 409
    assert problem["type"].endswith("lab-session-active")  # rendered as RFC 9457 when mounted

    # Paginated list: walk every page, each cursor and item addressed by stable_id.
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = f"limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        status, _, page = ex.call("GET", "/v1/lab-sessions", query=query)
        assert status == 200
        seen += [item["stable_id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert json.loads(_decoded_cursor(cursor))["s"] in session_ids
    assert seen == session_ids

    # Hints and files (success and error, including a provider failure).
    hints = f"{base}/hints"
    assert ex.call("POST", hints, body={"task_id": "t1", "level": 1}, headers=lang)[0] == 200
    assert ex.call("POST", hints, body={"task_id": "t1", "level": 3})[0] == 404
    files = f"{base}/files"
    assert ex.call("PUT", files, body={"files": {"main.py": "x"}})[0] == 200
    assert ex.call("PUT", files, body={"files": {"evil.sh": "x"}}, headers=lang)[0] == 422

    def unavailable(*_a: object, **_k: object) -> None:
        raise ProviderUnavailable(f"docker daemon row {provider_error_key}")

    real_copy_in = env.sandbox.copy_in
    env.sandbox.copy_in = unavailable
    assert ex.call("PUT", files, body={"files": {"main.py": "x"}}, headers=lang)[0] == 503
    env.sandbox.copy_in = real_copy_in

    plant(sid)  # now plant the sandbox reference too

    # Grading run: request, replay, read, throttled/in-progress error, SSE stream.
    status, headers, run = ex.call("POST", f"{base}/grading-runs", headers={"idempotency-key": "g"})
    assert status == 202
    rid = run["stable_id"]
    assert headers["location"] == f"{base}/grading-runs/{rid}"
    assert ex.call("POST", f"{base}/grading-runs", headers={"idempotency-key": "g"})[0] == 202
    assert ex.call("POST", f"{base}/grading-runs", headers={"idempotency-key": "g2"})[0] >= 400
    for check, outcome in (("c1", CheckOutcome.PASSED), ("c2", CheckOutcome.FAILED)):
        env.runs.append_result(
            rid, CheckResult(check, "t1", outcome, f"exit-{code_key}", f"fb.{check}")
        )
    assert env.runs.finish(rid, status=RunStatus.FAILED, graded_at=env.clock.now())
    status, _, run = ex.call("GET", f"{base}/grading-runs/{rid}", headers=lang)
    assert status == 200
    assert run["session_id"] == sid
    assert ex.call("GET", f"{base}/grading-runs/nope")[0] == 404
    status, headers, events = ex.call("GET", f"{base}/grading-runs/{rid}/events", headers=lang)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert len(events) == 3  # one per Check, then the run
    assert events[-1]["stable_id"] == rid and events[-1]["session_id"] == sid
    assert (
        ex.call("GET", f"{base}/grading-runs/{rid}/events", headers={"last-event-id": "1"})[0]
        == 200
    )

    # An unexpected repository failure is a generic 500 problem that names no key.
    real_get = env.sessions.get_for_owner

    def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError(f"row {repo_error_key} violated constraint")

    env.sessions.get_for_owner = broken
    assert ex.call("GET", base, headers=lang)[0] == 500
    env.sessions.get_for_owner = real_get

    # Reset and end close the flow.
    status, _, fresh = ex.call("POST", f"{base}/reset", headers={"idempotency-key": "r"})
    assert status == 201
    assert fresh["stable_id"] != sid
    assert ex.call("DELETE", f"/v1/lab-sessions/{fresh['stable_id']}")[0] == 200
    assert all(s < 600 for s in ex.statuses)
