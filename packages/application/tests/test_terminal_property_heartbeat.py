"""Property 16: heartbeat closes after consecutive missed pongs (task 10.4).

Feature: hands-on-labs, Property 16: Heartbeat closes after consecutive missed pongs

**Validates: Requirements 12.5**
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from edu_it_application.labs import Frame, TerminalAuthorization, TerminalGatewayCore
from edu_it_domain import config
from edu_it_domain.labs.limits import HeartbeatTracker
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st


def _load_gateway_helpers() -> object:
    """Load the sibling test module by path (tests are not an importable package)."""
    path = Path(__file__).with_name("test_terminal_gateway.py")
    spec = importlib.util.spec_from_file_location("_terminal_gateway_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_helpers = _load_gateway_helpers()
Env = _helpers.Env  # type: ignore[attr-defined]
FakeSocket = _helpers.FakeSocket  # type: ignore[attr-defined]

CLOSE_HEARTBEAT = 4408


@settings(max_examples=100, deadline=None)
@given(
    answers=st.lists(st.booleans(), min_size=1, max_size=30),
    max_missed=st.integers(min_value=1, max_value=5),
)
def test_tracker_closes_exactly_after_max_consecutive_missed_pongs(
    answers: list[bool], max_missed: int
) -> None:
    """Feature: hands-on-labs, Property 16: Heartbeat closes after consecutive missed pongs."""
    clock = Test_Clock()
    interval = config.TERMINAL_PING_INTERVAL
    tracker = HeartbeatTracker(clock, interval=interval, max_missed=max_missed)
    run = 0  # consecutive earlier pings left unanswered
    for answered in answers:
        clock.advance(interval)
        assert tracker.ping_due()
        tracker.record_ping()
        assert tracker.should_close == (run >= max_missed)
        if tracker.should_close:
            return
        if answered:
            tracker.record_pong()
            run = 0
        else:
            run += 1
        assert not tracker.should_close or run >= max_missed


class _AutoPongSocket(FakeSocket):
    """Answers the first ``answers`` pings with a pong, then stays silent."""

    def __init__(self, answers: int) -> None:
        super().__init__()
        self._answers = answers

    async def send(self, frame: Frame) -> None:
        await super().send(frame)
        if frame.text and '"ping"' in frame.text and self._answers > 0:
            self._answers -= 1
            await self.inbox.put(Frame(text='{"type":"pong"}'))


@settings(max_examples=100, deadline=None)
@given(
    answered=st.integers(min_value=0, max_value=4),
    max_missed=st.integers(min_value=1, max_value=4),
)
def test_gateway_closes_4408_after_consecutive_missed_pongs(answered: int, max_missed: int) -> None:
    """Feature: hands-on-labs, Property 16: Heartbeat closes after consecutive missed pongs."""
    cfg = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if not k.startswith("_")})
    cfg.TERMINAL_MAX_MISSED_PONGS = max_missed

    async def scenario() -> tuple[int, int, tuple[int, str] | None]:
        env = Env()
        env.add()

        async def sleep(_: float) -> None:
            env.clock.advance(config.TERMINAL_PING_INTERVAL + timedelta(seconds=1))
            await asyncio.sleep(0.002)

        gw = TerminalGatewayCore(
            sessions=env.sessions,
            sandbox=env.sandbox,
            clock=env.clock,
            provider_name="fake",
            config=cfg,
            sleep=sleep,
        )
        sock = _AutoPongSocket(answered)
        code = await asyncio.wait_for(gw.run(TerminalAuthorization("s1", "u1"), sock), 10)
        pings = sum(1 for t in sock.texts() if t["type"] == "ping")
        return code, pings, sock.closed

    code, pings, closed = asyncio.run(scenario())
    assert code == CLOSE_HEARTBEAT
    assert closed is not None and closed[0] == CLOSE_HEARTBEAT
    # Every answered ping resets the count; then max_missed unanswered pings
    # are sent before the next one finds the limit reached and closes.
    assert pings == answered + max_missed
