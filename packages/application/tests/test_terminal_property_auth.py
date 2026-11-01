"""Property 14: terminal connections are authorized and exclusive (task 10.2).

Feature: hands-on-labs, Property 14: Terminal connections are authorized and exclusive

**Validates: Requirements 12.1, 12.2, 12.10, 12.11, 13.2, 13.5, 26.2, 26.7**
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from edu_it_application.labs import (
    TerminalAuthorization,
    TerminalGatewayCore,
    TerminalRefusal,
)
from edu_it_application.labs.terminal import (
    CLOSE_ORIGIN,
    CLOSE_REPLACED,
    CLOSE_SESSION_REFUSED,
    CLOSE_UNAUTHENTICATED,
)
from edu_it_domain.labs.models import SessionStatus
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_terminal_gateway import (  # type: ignore[import-not-found]  # sibling, loaded by path
    ORIGIN,
    Env,
    FakeSocket,
)

# None = no cookie, "unknown" = cookie not in the store, else a known cookie.
_REFS = st.sampled_from([None, "unknown", "ref-u1", "ref-u2"])
_ORIGINS = st.sampled_from([None, ORIGIN, "https://evil.test"])
_TARGETS = st.sampled_from(["s1", "nope"])
_ACTIVE = st.sampled_from([SessionStatus.PROVISIONING, SessionStatus.READY, SessionStatus.GRADING])
_OWNER_REF = "ref-u1"


def _expected(
    ref: str | None, origin: str | None, target: str, status: SessionStatus | None
) -> Any:
    """Independent oracle for the four ordered connect checks."""
    if ref not in ("ref-u1", "ref-u2"):
        return CLOSE_UNAUTHENTICATED
    if origin != ORIGIN:
        return CLOSE_ORIGIN
    if target != "s1" or status is None or not status.is_active or ref != _OWNER_REF:
        return CLOSE_SESSION_REFUSED
    return "ok"


@settings(
    max_examples=100,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
class TerminalConnections(RuleBasedStateMachine):
    """Connects and reconnects against one session, in any auth/origin/state mix."""

    @initialize()
    def setup(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.env = Env()
        self.gateway: TerminalGatewayCore = self.env.gateway()
        self.status: SessionStatus | None = None
        self.sockets: list[FakeSocket] = []
        self.tasks: list[asyncio.Task[int]] = []
        self.live: FakeSocket | None = None
        self.refused_session: set[tuple[int, str]] = set()
        self.refused_auth: set[tuple[int, str]] = set()
        self.refused_origin: set[tuple[int, str]] = set()

    def teardown(self) -> None:
        async def finish() -> None:
            for sock in self.sockets:
                await sock.inbox.put(None)
            await asyncio.gather(*self.tasks, return_exceptions=True)

        self.loop.run_until_complete(finish())
        self.loop.close()

    @precondition(lambda self: self.status is None)
    @rule(status=_ACTIVE)
    def create_session(self, status: SessionStatus) -> None:
        self.env.add(status)
        self.status = status

    @precondition(lambda self: self.status is not None and self.status.is_active)
    @rule()
    def end_session(self) -> None:
        assert self.status is not None
        self.env.sessions.transition("s1", expected={self.status}, to=SessionStatus.ENDED)
        self.status = SessionStatus.ENDED

    @rule(ref=_REFS, origin=_ORIGINS, session_id=_TARGETS)
    def connect(self, ref: str | None, origin: str | None, session_id: str) -> None:
        result = self.env.auth.execute(session_ref=ref, origin=origin, session_id=session_id)
        want = _expected(ref, origin, session_id, self.status)
        if want != "ok":
            assert isinstance(result, TerminalRefusal)
            assert result.code == want
            bucket = {
                CLOSE_UNAUTHENTICATED: self.refused_auth,
                CLOSE_ORIGIN: self.refused_origin,
                CLOSE_SESSION_REFUSED: self.refused_session,
            }[want]
            bucket.add((result.code, result.reason))
            return
        assert result == TerminalAuthorization("s1", "u1")
        self.loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        """Accept one authorized connection and wait until it is registered."""
        previous = self.live
        sock = FakeSocket()
        self.sockets.append(sock)
        self.tasks.append(
            asyncio.ensure_future(self.gateway.run(TerminalAuthorization("s1", "u1"), sock))
        )
        for _ in range(500):
            await asyncio.sleep(0.005)
            if previous is not None and previous.closed is None:
                continue
            if len(self.gateway.registry) == 1 and (
                previous is None or previous.closed is not None
            ):
                break
        self.live = sock
        if previous is not None:
            assert previous.closed == (CLOSE_REPLACED, "replaced")

    @invariant()
    def refusals_are_uniform(self) -> None:
        # Unknown, non-active and foreign sessions share one refusal (13.2, 26.2).
        assert len(self.refused_session) <= 1
        assert len(self.refused_auth) <= 1
        assert len(self.refused_origin) <= 1
        assert not (self.refused_session & self.refused_auth)
        assert not (self.refused_session & self.refused_origin)

    @invariant()
    def at_most_one_live_connection(self) -> None:
        # Exclusivity (12.10): never more than one open socket per session.
        assert len(self.gateway.registry) <= 1
        open_sockets = [s for s in self.sockets if s.closed is None]
        assert len(open_sockets) <= 1
        if open_sockets:
            assert open_sockets[0] is self.live


TestTerminalConnections = TerminalConnections.TestCase
TestTerminalConnections.settings = settings(
    max_examples=100,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)
