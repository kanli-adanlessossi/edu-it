"""The Redis-backed sign-in ``SignInRateLimiter`` adapter (task 7.3, Req 5.1, 5.2, 12).

:class:`Redis_SignInRateLimiter` implements the domain
:class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter` port over Redis,
counting failed sign-in attempts per identifier within a rolling window. While an
identifier's failures exceed the configured threshold within the window,
:meth:`is_blocked` returns ``True`` so the sign-in flow rejects the attempt
*without evaluating credentials* and establishes no session even for valid
credentials (Req 12.1, 12.4). It is substitutable for the in-memory fake without
changing domain code (Req 5.2).

Business constants, not hardcoded (Req 12.3): the threshold and window are read
from :data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_THRESHOLD` and
:data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_WINDOW`, the single framework-free
home for the account system's business numbers.

Window measured by the injected Clock (Req 12.2): each identifier's counter
records the instant its current window opened. A failure that arrives after the
window has elapsed (per the injected :class:`~edu_it_domain.ports.clock.Clock`)
starts a fresh window, so attempts are allowed again once the window passes — and
a controllable ``Test_Clock`` can drive that boundary deterministically in
test/dev. A Redis key TTL is set as a self-cleaning backstop only; the Clock, not
Redis eviction, decides when a window has elapsed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import redis
from edu_it_domain.config import (
    SIGN_IN_RATE_LIMIT_THRESHOLD,
    SIGN_IN_RATE_LIMIT_WINDOW,
)
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.rate_limiter import SignInRateLimiter

__all__ = ["Redis_SignInRateLimiter"]

_TTL_GRACE = timedelta(seconds=30)
"""Extra time added to the backstop key TTL beyond the logical window."""


class Redis_SignInRateLimiter(SignInRateLimiter):
    """A :class:`SignInRateLimiter` backed by Redis counters (Req 12).

    Satisfies the :class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter`
    protocol, so the sign-in flow consults and updates it exactly as it does the
    in-memory fake.
    """

    def __init__(
        self,
        client: redis.Redis,
        clock: Clock,
        *,
        namespace: str = "edu_it:signin_rl",
        threshold: int = SIGN_IN_RATE_LIMIT_THRESHOLD,
        window: timedelta = SIGN_IN_RATE_LIMIT_WINDOW,
    ) -> None:
        """Create a rate limiter over ``client`` measuring time with ``clock``.

        ``threshold`` and ``window`` default to the domain business constants
        (Req 12.3); they are parameters only so a test can exercise small bounds.
        ``namespace`` prefixes every Redis key so rate-limit keys never collide
        with the task-queue or session keys sharing the same Redis server.
        """
        self._redis = client
        self._clock = clock
        self._namespace = namespace
        self._threshold = threshold
        self._window = window

    def _key(self, identifier: str) -> str:
        return f"{self._namespace}:{identifier}"

    @staticmethod
    def _as_str(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _current_count(self, identifier: str) -> int:
        """Return the failure count in the *current* window, or 0.

        A stored window whose start is older than :attr:`_window` ago (per the
        injected Clock) has elapsed, so its count no longer applies and 0 is
        returned — this is what lets attempts resume after the window passes
        (Req 12.2) without waiting on Redis eviction.
        """
        stored = self._redis.hgetall(self._key(identifier))
        if not stored:
            return 0
        record = {self._as_str(k): self._as_str(v) for k, v in dict(stored).items()}
        raw_start = record.get("window_start")
        raw_count = record.get("count")
        if raw_start is None or raw_count is None:
            return 0
        window_start = _from_iso(raw_start)
        if self._clock.now() - window_start >= self._window:
            return 0
        try:
            return int(raw_count)
        except ValueError:
            return 0

    def is_blocked(self, identifier: str) -> bool:
        """Return whether ``identifier`` has exceeded the threshold this window.

        ``True`` while the current-window failure count is greater than the
        configured threshold, consulted before credentials are evaluated
        (Req 12.1, 12.4). Becomes ``False`` again once the window elapses per the
        injected Clock (Req 12.2).
        """
        return self._current_count(identifier) > self._threshold

    def register_failure(self, identifier: str) -> None:
        """Record one failed sign-in attempt for ``identifier``.

        If no window is open or the open window has elapsed (per the Clock), a
        fresh window is started at the current instant with a count of one;
        otherwise the current window's count is incremented. A backstop key TTL
        of the window length plus a small grace self-cleans idle counters.
        """
        key = self._key(identifier)
        now = self._clock.now()
        count = self._current_count(identifier)
        if count == 0:
            new_count = 1
            window_start = now
        else:
            new_count = count + 1
            window_start = self._window_start(identifier, fallback=now)
        self._redis.hset(
            key,
            mapping={"window_start": _to_iso(window_start), "count": str(new_count)},
        )
        ttl_seconds = int((self._window + _TTL_GRACE).total_seconds())
        self._redis.expire(key, ttl_seconds)

    def reset(self, identifier: str) -> None:
        """Clear the failure counter for ``identifier`` (called on success)."""
        self._redis.delete(self._key(identifier))

    def _window_start(self, identifier: str, *, fallback: datetime) -> datetime:
        """Return the stored window-start instant, or ``fallback`` when absent."""
        stored = self._redis.hget(self._key(identifier), "window_start")
        if stored is None:
            return fallback
        return _from_iso(self._as_str(stored))


def _to_iso(instant: datetime) -> str:
    """Serialize an aware instant to ISO-8601 in UTC for storage."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    """Parse a stored ISO-8601 instant back to an aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
