"""Property test for lab session list pagination (task 7.4).

# Feature: hands-on-labs, Property 29: Session lists page without gaps or repeats

For any set of a learner's sessions and any page size, following cursors through
``GET /v1/lab-sessions`` returns every one of the caller's sessions exactly once in
``(created_at, stable_id)`` order, never another user's session, and never exposes an
internal numeric key; a malformed or expired cursor is rejected.

**Validates: Requirements 17.5**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any

from edu_it_api.errors import install_error_handlers
from edu_it_api.labs import LabServices, build_labs_router
from edu_it_api.versioning import build_version_app
from edu_it_application.labs import (
    EndSessionUseCase,
    GetSessionUseCase,
    ListSessionsUseCase,
    ResetLabUseCase,
    RevealLabHintUseCase,
    SaveFilesUseCase,
    StartLabUseCase,
)
from edu_it_application.learning.pagination import CursorCodec
from edu_it_domain.config import CURSOR_TTL, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from edu_it_domain.labs.models import LabSession, SessionStatus
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
from hypothesis import given, settings
from hypothesis import strategies as st

_USERS = ("u1", "u2", "u3")
_CALLER = "u1"
_BASE = datetime(2030, 1, 1, tzinfo=UTC)
_SCOPE = "lab-sessions"


class _Env:
    """An isolated labs router over in-memory repositories and a Test_Clock."""

    def __init__(self) -> None:
        self.clock = Test_Clock()
        runs = In_Memory_GradingRunRepository()
        self.sessions = In_Memory_LabSessionRepository(runs)
        queue = In_Memory_TaskQueue()
        catalog = Fake_LabCatalog([])
        ids = Sequential_IdGenerator("ses")
        end = EndSessionUseCase(sessions=self.sessions, queue=queue, clock=self.clock)
        start = StartLabUseCase(
            sessions=self.sessions,
            catalog=catalog,
            queue=queue,
            idempotency=In_Memory_IdempotencyStore(),
            clock=self.clock,
            ids=ids,
        )
        self.codec = CursorCodec(self.clock)
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
                sandbox=Fake_SandboxProvider(self.clock),
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
            cursor_codec=self.codec,
            clock=self.clock,
        )
        self.app = build_version_app(1, [build_labs_router(services)])
        install_error_handlers(self.app)

    def seed(self, rows: list[tuple[str, str, SessionStatus, int]]) -> None:
        """Insert ``(stable_id, user, status, created offset seconds)`` rows directly."""
        for sid, user, status, offset in rows:
            created = _BASE + timedelta(seconds=offset)
            self.sessions._sessions[sid] = LabSession(
                stable_id=sid,
                user_id=user,
                lab_id="lab1",
                status=status,
                created_at=created,
                ttl_at=created + timedelta(hours=1),
            )

    def get(self, query: str, user: str = _CALLER) -> tuple[int, Any]:
        return _get(self.app, "/lab-sessions", query, {"x-user": user})


def _get(app: Any, path: str, query: str, headers: dict[str, str]) -> tuple[int, Any]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
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
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload) if payload else None


def _ints(value: Any) -> list[int]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _ints(v)]
    if isinstance(value, list):
        return [x for v in value for x in _ints(v)]
    return [value] if isinstance(value, int) and not isinstance(value, bool) else []


# Few distinct offsets relative to the row count force many identical timestamps.
_row = st.tuples(
    st.sampled_from(_USERS),
    st.sampled_from(list(SessionStatus)),
    st.integers(min_value=0, max_value=4),
)
_limits = st.one_of(
    st.none(),
    st.integers(min_value=1, max_value=7),
    st.sampled_from([1, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE]),
)


@settings(max_examples=100, deadline=None)
@given(rows=st.lists(_row, max_size=30), limit=_limits)
def test_session_lists_page_without_gaps_or_repeats(
    rows: list[tuple[str, SessionStatus, int]], limit: int | None
) -> None:
    # Feature: hands-on-labs, Property 29: Session lists page without gaps or repeats
    env = _Env()
    # Zero-padded ids sort lexically, but are assigned in a scrambled order vs. insertion.
    seeded = [
        (f"s{(i * 7) % 31:03d}-{i:03d}", user, status, offset)
        for i, (user, status, offset) in enumerate(rows)
    ]
    env.seed(seeded)
    expected = [
        sid
        for sid, _u, _s, _o in sorted(
            (r for r in seeded if r[1] == _CALLER), key=lambda r: (r[3], r[0])
        )
    ]
    page_size = DEFAULT_PAGE_SIZE if limit is None else limit

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(len(expected) + 2):
        query = "" if limit is None else f"limit={limit}"
        if cursor is not None:
            query += ("&" if query else "") + f"cursor={cursor}"
        status, body = env.get(query)
        assert status == 200, body
        assert len(body["items"]) <= page_size
        assert not _ints(body), "no integer (internal key) may appear in a page"
        seen += [item["stable_id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
        # A cursor is opaque: it decodes only to (timestamp, stable_id) and has no row key.
        key = env.codec.decode(_SCOPE, cursor)
        assert isinstance(key[0], datetime)
        assert isinstance(key[1], str)
        assert key[1] in expected
        assert len(body["items"]) == page_size  # only a full page has a successor
    else:
        raise AssertionError("pagination did not terminate")

    assert seen == expected  # no gaps, no repeats, caller-only, stable order


@settings(max_examples=100, deadline=None)
@given(count=st.integers(min_value=2, max_value=8))
def test_malformed_or_expired_cursor_is_rejected(count: int) -> None:
    # Feature: hands-on-labs, Property 29: Session lists page without gaps or repeats
    env = _Env()
    env.seed([(f"s{i:03d}", _CALLER, SessionStatus.ENDED, i) for i in range(count)])
    status, page = env.get("limit=1")
    assert status == 200
    cursor = page["next_cursor"]
    assert cursor is not None
    assert env.get("cursor=not-a-cursor")[0] == 400
    assert env.get(f"cursor={cursor}x")[0] == 400
    env.clock.advance(CURSOR_TTL + timedelta(seconds=1))
    assert env.get(f"limit=1&cursor={cursor}")[0] == 400
