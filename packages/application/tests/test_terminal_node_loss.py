"""Node-loss close and reconnect re-verification (kubernetes-deployment task 9.3)."""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from edu_it_application.labs import AuthorizeTerminal, TerminalAuthorization, TerminalRefusal
from edu_it_application.labs.terminal import CLOSE_DRAIN, CLOSE_SESSION_REFUSED
from edu_it_domain import config
from edu_it_domain.labs.models import SessionStatus

sys.path.insert(0, str(Path(__file__).parent))
from test_terminal_gateway import (  # type: ignore[import-not-found]  # sibling, loaded by path
    ORIGIN,
    Env,
    FakeSocket,
)

_FORBIDDEN = ("pod", "node", "kube", "evict", "sandbox", "drain")


def test_node_loss_closes_with_translated_message_and_reconnect() -> None:
    async def scenario() -> tuple[FakeSocket, timedelta]:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await asyncio.sleep(0.05)
        env.sessions.transition(
            "s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED_BY_INFRASTRUCTURE
        )
        start = env.clock.now()
        env.clock.advance(config.TERMINAL_STATUS_POLL_INTERVAL)
        await asyncio.wait_for(task, 5)
        return sock, env.clock.now() - start

    sock, elapsed = asyncio.run(scenario())
    texts = sock.texts()
    assert elapsed <= config.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND
    assert any(t.get("message_key") == "labs.session_interrupted" for t in texts)
    assert any(t["type"] == "reconnect" for t in texts)
    assert sock.closed is not None and sock.closed[0] == CLOSE_DRAIN
    blob = (str(texts) + sock.closed[1]).lower()
    assert not any(word in blob for word in _FORBIDDEN)


def test_poll_interval_fits_the_node_loss_bound() -> None:
    assert config.TERMINAL_STATUS_POLL_INTERVAL < config.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND


def test_reconnect_refused_without_attaching_for_ownership_expiry_and_infra_loss() -> None:
    env = Env()
    env.add()
    auth = AuthorizeTerminal(sessions=env.sessions, session_store=env.store, clock=env.clock)
    ok = auth.execute(session_ref="ref-u1", origin=ORIGIN, session_id="s1")
    assert ok == TerminalAuthorization("s1", "u1")

    foreign = auth.execute(session_ref="ref-u2", origin=ORIGIN, session_id="s1")
    assert isinstance(foreign, TerminalRefusal) and foreign.code == CLOSE_SESSION_REFUSED

    env.clock.advance(config.LAB_SESSION_TTL + timedelta(seconds=1))
    env.store.create("ref-u1", "u1", expires_at=env.clock.now() + timedelta(days=1))
    expired = auth.execute(session_ref="ref-u1", origin=ORIGIN, session_id="s1")
    assert expired == foreign


def test_run_refuses_expired_session_without_attaching() -> None:
    async def scenario() -> tuple[int, int, FakeSocket]:
        env = Env()
        env.add()
        env.clock.advance(config.LAB_SESSION_TTL + timedelta(seconds=1))
        sock = FakeSocket()
        code = await env.gateway().run(TerminalAuthorization("s1", "u1"), sock)
        return code, len(env.sandbox.list_sandboxes()), sock

    code, _, sock = asyncio.run(scenario())
    assert code == CLOSE_SESSION_REFUSED
    assert sock.closed is not None and sock.sent == []


def test_run_refuses_after_infra_loss_and_foreign_owner() -> None:
    async def scenario() -> tuple[int, int]:
        env = Env()
        env.add(SessionStatus.ENDED_BY_INFRASTRUCTURE)
        a = await env.gateway().run(TerminalAuthorization("s1", "u1"), FakeSocket())
        env2 = Env()
        env2.add()
        b = await env2.gateway().run(TerminalAuthorization("s1", "u2"), FakeSocket())
        return a, b

    assert asyncio.run(scenario()) == (CLOSE_SESSION_REFUSED, CLOSE_SESSION_REFUSED)


# --- Task 9.7: additional unit tests -------------------------------------------------------


# Claims: 19.1
def test_poll_interval_leaves_room_for_a_missed_poll_within_the_bound() -> None:
    assert 2 * config.TERMINAL_STATUS_POLL_INTERVAL <= config.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND


# Claims: 19.1
def test_socket_stays_open_until_the_loss_and_closes_on_the_next_poll() -> None:
    async def scenario() -> tuple[bool, FakeSocket, timedelta]:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await asyncio.sleep(0.05)
        env.clock.advance(config.TERMINAL_STATUS_POLL_INTERVAL)
        await asyncio.sleep(0.05)
        open_before_loss = sock.closed is None and not task.done()
        env.sessions.transition(
            "s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED_BY_INFRASTRUCTURE
        )
        detected = env.clock.now()
        env.clock.advance(config.TERMINAL_STATUS_POLL_INTERVAL)
        await asyncio.wait_for(task, 5)
        return open_before_loss, sock, env.clock.now() - detected

    open_before_loss, sock, elapsed = asyncio.run(scenario())
    assert open_before_loss
    assert sock.closed is not None and sock.closed[0] == CLOSE_DRAIN
    assert (
        elapsed <= config.TERMINAL_STATUS_POLL_INTERVAL < config.LAB_NODE_LOSS_TERMINAL_CLOSE_BOUND
    )


# Claims: 19.1, 20.1
def test_interrupted_message_precedes_reconnect_indication_and_close() -> None:
    async def scenario() -> FakeSocket:
        env = Env()
        env.add()
        sock = FakeSocket()
        task = asyncio.create_task(env.gateway().run(TerminalAuthorization("s1", "u1"), sock))
        await asyncio.sleep(0.05)
        env.sessions.transition(
            "s1", expected={SessionStatus.READY}, to=SessionStatus.ENDED_BY_INFRASTRUCTURE
        )
        env.clock.advance(config.TERMINAL_STATUS_POLL_INTERVAL)
        await asyncio.wait_for(task, 5)
        return sock

    texts = asyncio.run(scenario()).texts()
    kinds = [t["type"] for t in texts]
    assert "reconnect" in kinds
    interrupted = next(i for i, t in enumerate(texts) if t.get("message_key"))
    assert interrupted < kinds.index("reconnect")


# Claims: 19.1
def test_interrupted_message_is_translated_in_both_locales_without_infrastructure_detail() -> None:
    from edu_it_api.errors.labs import LAB_MESSAGES  # type: ignore[import-not-found]

    problem = LAB_MESSAGES["labs.session_interrupted"]
    for locale in ("fr", "en"):
        for text in (problem.title[locale], problem.detail[locale]):
            assert text
            assert not any(word in text.lower() for word in _FORBIDDEN)
    assert problem.detail["fr"] != problem.detail["en"]


# Claims: 14.8, 20.4
def test_ownership_failure_closes_without_touching_the_session_or_sandbox() -> None:
    async def scenario() -> tuple[int, FakeSocket, int, SessionStatus]:
        env = Env()
        env.add()
        before = len(env.sandbox.list_sandboxes())
        sock = FakeSocket()
        code = await env.gateway().run(TerminalAuthorization("s1", "u2"), sock)
        session = env.sessions.get("s1")
        assert session is not None
        return code, sock, len(env.sandbox.list_sandboxes()) - before, session.status

    code, sock, sandbox_delta, status = asyncio.run(scenario())
    assert code == CLOSE_SESSION_REFUSED
    assert sock.closed is not None and sock.closed[0] == CLOSE_SESSION_REFUSED
    assert sock.sent == []
    assert sandbox_delta == 0
    assert status is SessionStatus.READY


# Claims: 14.8, 20.4
def test_refusal_close_reason_has_no_infrastructure_detail() -> None:
    async def scenario() -> FakeSocket:
        env = Env()
        env.add(SessionStatus.ENDED_BY_INFRASTRUCTURE)
        sock = FakeSocket()
        await env.gateway().run(TerminalAuthorization("s1", "u1"), sock)
        return sock

    sock = asyncio.run(scenario())
    assert sock.closed is not None
    assert not any(word in sock.closed[1].lower() for word in _FORBIDDEN)
