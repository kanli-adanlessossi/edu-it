"""The sign-in ``SignInRateLimiter`` port (Req 5.1, 12.1-12.4).

Repeated failed sign-ins for one identifier are slowed by counting failures
within a rolling window and blocking further attempts once the count exceeds the
configured threshold, *without evaluating the submitted credentials* and
establishing no session even for valid credentials (Req 12.1, 12.4). When the
window elapses — measured by the injected ``Clock`` — attempts are allowed again
(Req 12.2). The threshold and window are the business constants
:data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_THRESHOLD` and
:data:`~edu_it_domain.config.SIGN_IN_RATE_LIMIT_WINDOW` (Req 12.3); the concrete
store reads them rather than hardcoding.

This port captures the two decisions the sign-in flow needs: ask whether an
identifier is currently blocked (checked *before* evaluating credentials), and
record a failed attempt. A successful sign-in clears the identifier's counter
through :meth:`reset`. The implementation keeps per-identifier counters in Redis
(ADR 0003/0004 share the Redis server); tests use an in-memory one.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["SignInRateLimiter"]


@runtime_checkable
class SignInRateLimiter(Protocol):
    """Port for rate-limiting failed sign-in attempts per identifier (Req 12)."""

    def is_blocked(self, identifier: str) -> bool:
        """Return whether ``identifier`` is currently over the failure threshold.

        Checked *before* credentials are evaluated: while this is ``True`` the
        sign-in flow rejects the attempt without evaluating the submitted
        credentials and establishes no session (Req 12.1, 12.4). It becomes
        ``False`` again once the window elapses as measured by the injected
        ``Clock`` (Req 12.2).
        """
        ...

    def register_failure(self, identifier: str) -> None:
        """Record one failed sign-in attempt for ``identifier``.

        The failure is counted within the current window; once the count exceeds
        the configured threshold, :meth:`is_blocked` returns ``True`` for the
        identifier until the window elapses.
        """
        ...

    def reset(self, identifier: str) -> None:
        """Clear the failure counter for ``identifier``.

        Called after a successful sign-in so a user who eventually authenticates
        is not penalised by earlier failures.
        """
        ...
