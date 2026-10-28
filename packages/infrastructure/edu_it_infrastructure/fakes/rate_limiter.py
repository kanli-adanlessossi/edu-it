"""In-memory sign-in ``SignInRateLimiter`` fake (Req 5.2, 12).

:class:`In_Memory_SignInRateLimiter` implements the domain
:class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter` port with a plain
in-process dictionary, so the sign-in use case (task 17.2) and its property test
(task 17.4) can be exercised without a live Redis. It is substitutable for the
Redis-backed
:class:`~edu_it_infrastructure.adapters.rate_limiter.Redis_SignInRateLimiter`
without changing domain code (Req 5.2): it satisfies the same protocol and
enforces the same Req 12 semantics.

The fake mirrors the adapter's two load-bearing behaviors exactly:

- **Threshold is strict.** :meth:`is_blocked` is ``True`` only once the
  current-window failure count is *greater than* the configured threshold, so up
  to and including the threshold still allows attempts, matching the adapter
  (Req 12.1).
- **Window measured by the Clock.** Each identifier's counter records the instant
  its window opened; a failure that arrives at or after the window has elapsed
  (per the injected :class:`~edu_it_domain.ports.clock.Clock`) starts a fresh
  window, so attempts are allowed again once the window passes — a controllable
  ``Test_Clock`` drives that boundary deterministically (Req 12.2).

The threshold and window default to the domain business constants
:data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_THRESHOLD` and
:data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_WINDOW` (Req 12.3); they are
parameters only so a test can exercise small bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from edu_it_domain.config import (
    SIGN_IN_RATE_LIMIT_THRESHOLD,
    SIGN_IN_RATE_LIMIT_WINDOW,
)
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.rate_limiter import SignInRateLimiter

__all__ = ["In_Memory_SignInRateLimiter"]


@dataclass(slots=True)
class _Window:
    """One identifier's current window: when it opened and its failure count."""

    window_start: datetime
    count: int


class In_Memory_SignInRateLimiter(SignInRateLimiter):
    """A :class:`SignInRateLimiter` backed by an in-process dictionary (Req 12).

    Satisfies the :class:`~edu_it_domain.ports.rate_limiter.SignInRateLimiter`
    protocol, so the sign-in flow consults and updates it exactly as it does the
    Redis adapter. Expiry of a window is decided against the injected ``Clock``,
    so the fake honours a controllable ``Test_Clock`` just like the adapter.
    """

    __test__ = False

    def __init__(
        self,
        clock: Clock,
        *,
        threshold: int = SIGN_IN_RATE_LIMIT_THRESHOLD,
        window: timedelta = SIGN_IN_RATE_LIMIT_WINDOW,
    ) -> None:
        """Create an empty limiter measuring the window with ``clock``.

        ``threshold`` and ``window`` default to the domain business constants
        (Req 12.3); they are parameters only so a test can exercise small bounds.
        """
        self._clock = clock
        self._threshold = threshold
        self._window = window
        self._by_identifier: dict[str, _Window] = {}

    def _current_count(self, identifier: str) -> int:
        """Return the failure count in the *current* window, or 0.

        A stored window whose start is older than :attr:`_window` ago (per the
        injected Clock) has elapsed, so its count no longer applies and 0 is
        returned — this is what lets attempts resume after the window passes
        (Req 12.2).
        """
        entry = self._by_identifier.get(identifier)
        if entry is None:
            return 0
        if self._clock.now() - entry.window_start >= self._window:
            return 0
        return entry.count

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
        otherwise the current window's count is incremented.
        """
        now = self._clock.now()
        count = self._current_count(identifier)
        if count == 0:
            self._by_identifier[identifier] = _Window(window_start=now, count=1)
        else:
            self._by_identifier[identifier].count = count + 1

    def reset(self, identifier: str) -> None:
        """Clear the failure counter for ``identifier`` (called on success)."""
        self._by_identifier.pop(identifier, None)
