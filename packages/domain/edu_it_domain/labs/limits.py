"""Terminal message limits and heartbeat tracking (hands-on-labs Req 12.5, 12.9).

``TokenBucket`` limits message rate and ``HeartbeatTracker`` counts missed
pongs; both read time only through the injected ``Clock``.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from edu_it_domain import config as _cfg

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from edu_it_domain.ports.clock import Clock

__all__ = [
    "HeartbeatTracker",
    "MessageLimiter",
    "MessageVerdict",
    "TokenBucket",
]


class TokenBucket:
    """A token bucket refilled continuously at ``rate_per_second`` up to ``capacity``."""

    def __init__(self, clock: Clock, capacity: int, rate_per_second: float) -> None:
        if capacity <= 0 or rate_per_second <= 0:
            raise ValueError("capacity and rate_per_second must be positive")
        self._clock = clock
        self._capacity = capacity
        self._rate = rate_per_second
        self._tokens = float(capacity)
        self._last = clock.now()

    def _refill(self) -> None:
        now = self._clock.now()
        elapsed = (now - self._last).total_seconds()
        if elapsed > 0:
            self._tokens = min(float(self._capacity), self._tokens + elapsed * self._rate)
        self._last = max(self._last, now)

    def try_consume(self, tokens: int = 1) -> bool:
        """Take ``tokens`` if available; return whether the request is allowed."""

        self._refill()
        if tokens <= self._tokens:
            self._tokens -= tokens
            return True
        return False


class MessageVerdict(Enum):
    """Result of checking one terminal message; values are WebSocket close codes."""

    OK = 0
    TOO_BIG = 1009
    RATE_EXCEEDED = 1008


class MessageLimiter:
    """Per-connection size and rate limit (64 KiB, 1000 messages/s by default)."""

    def __init__(
        self,
        clock: Clock,
        max_bytes: int = _cfg.TERMINAL_MAX_MESSAGE_BYTES,
        messages_per_second: int = _cfg.TERMINAL_MAX_MESSAGES_PER_SECOND,
    ) -> None:
        self._max_bytes = max_bytes
        self._bucket = TokenBucket(clock, messages_per_second, messages_per_second)

    def check(self, size_bytes: int) -> MessageVerdict:
        if size_bytes > self._max_bytes:
            return MessageVerdict.TOO_BIG
        if not self._bucket.try_consume():
            return MessageVerdict.RATE_EXCEEDED
        return MessageVerdict.OK


class HeartbeatTracker:
    """Counts consecutive pings that went unanswered by a pong."""

    def __init__(
        self,
        clock: Clock,
        interval: timedelta = _cfg.TERMINAL_PING_INTERVAL,
        max_missed: int = _cfg.TERMINAL_MAX_MISSED_PONGS,
    ) -> None:
        self._clock = clock
        self._interval = interval
        self._max_missed = max_missed
        self._last_ping: datetime = clock.now()
        self._awaiting_pong = False
        self._missed = 0

    @property
    def missed(self) -> int:
        return self._missed

    @property
    def should_close(self) -> bool:
        """True once ``max_missed`` consecutive pongs have been missed."""

        return self._missed >= self._max_missed

    def ping_due(self) -> bool:
        return self._clock.now() - self._last_ping >= self._interval

    def record_ping(self) -> None:
        """Record a sent ping; a still-unanswered previous ping counts as missed."""

        if self._awaiting_pong:
            self._missed += 1
        self._awaiting_pong = True
        self._last_ping = self._clock.now()

    def record_pong(self) -> None:
        self._awaiting_pong = False
        self._missed = 0
