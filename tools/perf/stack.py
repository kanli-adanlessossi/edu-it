"""Helpers shared by the measurement drivers and the stack bootstrap (task 21.5).

Everything here talks to the REAL stack: PostgreSQL and Redis through the project's own
adapters, and the served API over HTTP and WebSocket. Nothing is mocked or simulated.

Authentication for measurement: the API now has sign-up and sign-in routes, but the harness
does not use them. It mints a learner directly by inserting a verified ``user`` row and
creating a server-side session in the Redis ``SessionStore`` (the same store the API
resolves the ``edu_it_session`` cookie through). The CSRF double-submit pair is
generated client-side, as the web app does.

Environment: ``DATABASE_URL`` and ``REDIS_URL`` (as the api/worker), ``PERF_API_BASE``
(default ``http://127.0.0.1:8000``), ``PERF_CONTENT_ROOT`` (default ``content``).
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import timedelta
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

API_BASE = os.environ.get("PERF_API_BASE", "http://127.0.0.1:8000")
ORIGIN = "http://localhost:3000"  # the single entry of LAB_ALLOWED_WEB_ORIGINS
LAB_ID = "lab.fix-the-shared-folder"
SESSION_COOKIE = "edu_it_session"
STATE_DIR = Path(__file__).resolve().parent / ".state"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Learner:
    user_id: str
    ref: str
    csrf: str

    def cookie(self) -> str:
        return f"{SESSION_COOKIE}={self.ref}; csrf_token={self.csrf}"


# -- database seed -------------------------------------------------------------------------


def seed_database(content_root: str | Path | None = None) -> dict[str, int]:
    """Sync the authored content into the relational catalog (idempotent, by ``stable_id``).

    Delegates to the same ``sync_content`` that ``edu-it-content sync --database-url``
    runs, through ``Postgres_ContentStore``, so the foreign keys a Lab_Session needs and
    the quiz, project and prerequisite rows are all written. A second call changes no
    row. Returns the number of items inserted, updated, unchanged and archived.
    """
    from edu_it_content.postgres_store import Postgres_ContentStore
    from edu_it_content.sync import sync_content
    from edu_it_content.validation import load_content
    from edu_it_infrastructure.composition import CompositionSettings
    from sqlalchemy import create_engine

    root = Path(content_root or os.environ.get("PERF_CONTENT_ROOT", REPO_ROOT / "content"))
    engine = create_engine(CompositionSettings.from_env().database_url)
    try:
        report = sync_content(Postgres_ContentStore(engine), load_content(root))
    finally:
        engine.dispose()
    return {
        "inserted": len(report.inserted),
        "updated": len(report.updated),
        "unchanged": len(report.unchanged),
        "archived": len(report.archived),
    }

# -- learners -------------------------------------------------------------------------------


def mint_learner() -> Learner:
    """Create a verified user and a one-hour server-side session; return the cookie pair."""
    from edu_it_domain.ports.repository import USER_STATUS_VERIFIED, UserRecord
    from edu_it_infrastructure.adapters.redis_support import redis_from_url
    from edu_it_infrastructure.adapters.session_store import Redis_SessionStore
    from edu_it_infrastructure.clock import System_Clock
    from edu_it_infrastructure.composition import CompositionSettings
    from edu_it_infrastructure.persistence.repositories import Postgres_UserRepository

    settings = CompositionSettings.from_env()
    clock = System_Clock()
    user_id = f"perf-{uuid.uuid4().hex[:24]}"
    Postgres_UserRepository(settings.database_url).add(
        UserRecord(
            stable_id=user_id,
            email=f"{user_id}@example.test",
            locale="en",
            status=USER_STATUS_VERIFIED,
        )
    )
    ref = secrets.token_urlsafe(32)
    Redis_SessionStore(redis_from_url(settings.redis_url), clock).create(
        ref, user_id, expires_at=clock.now() + timedelta(hours=1)
    )
    return Learner(user_id=user_id, ref=ref, csrf=secrets.token_urlsafe(32))


# -- HTTP -----------------------------------------------------------------------------------


def api(
    method: str,
    path: str,
    learner: Learner,
    body: Any = None,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, Any]:
    """One request to the served API under ``/v1``; returns ``(status, parsed JSON | None)``."""
    parts = urlsplit(API_BASE)
    conn = HTTPConnection(parts.hostname, parts.port or 80, timeout=60)
    headers = {
        "Cookie": learner.cookie(),
        "X-CSRF-Token": learner.csrf,
        "Origin": ORIGIN,
        "Accept": "application/json",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    try:
        conn.request(method, f"/v1{path}", body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


def start_session(learner: Learner) -> dict[str, Any]:
    status, data = api(
        "POST", "/lab-sessions", learner, {"lab_id": LAB_ID}, idempotency_key=str(uuid.uuid4())
    )
    if status != 201:
        raise RuntimeError(f"start failed: {status} {data}")
    return data  # type: ignore[no-any-return]


def wait_ready(learner: Learner, session_id: str, *, timeout_s: float = 120.0) -> dict[str, Any]:
    """Poll the session every 25 ms until ``ready``; raise on ``failed`` or timeout."""
    deadline = time.perf_counter() + timeout_s
    while True:
        status, data = api("GET", f"/lab-sessions/{session_id}", learner)
        if status != 200:
            raise RuntimeError(f"session read failed: {status} {data}")
        if data["status"] == "ready":
            return data  # type: ignore[no-any-return]
        if data["status"] in ("failed", "ended", "expired", "idle-expired"):
            raise RuntimeError(f"session ended up {data['status']}: {data.get('error')}")
        if time.perf_counter() > deadline:
            raise RuntimeError("timed out waiting for ready")
        time.sleep(0.025)


def end_session(learner: Learner, session_id: str) -> None:
    api("DELETE", f"/lab-sessions/{session_id}", learner)


# -- WebSocket terminal ----------------------------------------------------------------------


def ws_connect(learner: Learner, session_id: str) -> Any:
    from websockets.sync.client import connect

    parts = urlsplit(API_BASE)
    return connect(
        f"ws://{parts.hostname}:{parts.port or 80}/v1/lab-sessions/{session_id}/terminal",
        additional_headers={"Origin": ORIGIN, "Cookie": learner.cookie()},
        compression=None,
        open_timeout=30,
        max_size=None,
    )


def drain_until_quiet(ws: Any, quiet_s: float = 0.4, max_s: float = 5.0) -> bytes:
    """Read terminal output until it has been silent for ``quiet_s`` (the shell prompt)."""
    out = bytearray()
    end = time.perf_counter() + max_s
    while time.perf_counter() < end:
        try:
            msg = ws.recv(timeout=quiet_s)
        except TimeoutError:
            break
        if isinstance(msg, bytes):
            out += msg
    return bytes(out)


def read_until(ws: Any, needle: bytes, *, timeout_s: float) -> bytes:
    out = bytearray()
    end = time.perf_counter() + timeout_s
    while needle not in out:
        left = end - time.perf_counter()
        if left <= 0:
            raise TimeoutError(f"did not see {needle!r} within {timeout_s}s; got {bytes(out)!r}")
        msg = ws.recv(timeout=left)
        if isinstance(msg, bytes):
            out += msg
    return bytes(out)


# -- driver state ------------------------------------------------------------------------------


def load_state(name: str) -> dict[str, Any] | None:
    path = STATE_DIR / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_state(name: str, learner: Learner, session_id: str, **extra: Any) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{name}.json").write_text(
        json.dumps({"learner": asdict(learner), "session_id": session_id, **extra})
    )


def ensure_ready_session(name: str) -> tuple[Learner, str, bool]:
    """A ready session for the named driver, created (untimed) when missing or no longer ready.

    Returns ``(learner, session_id, created)``.
    """
    state = load_state(name)
    if state is not None:
        learner = Learner(**state["learner"])
        status, data = api("GET", f"/lab-sessions/{state['session_id']}", learner)
        if status == 200 and data["status"] == "ready":
            return learner, state["session_id"], False
    learner = mint_learner()
    session = start_session(learner)
    wait_ready(learner, session["stable_id"])
    save_state(name, learner, session["stable_id"])
    return learner, session["stable_id"], True


def emit_seconds(seconds: float, **extra: Any) -> None:
    """Report the sample on the last stdout line, as ``lab_perf.reported_seconds`` expects."""
    print(json.dumps({"seconds": seconds, **extra}))
