"""The ``Clock`` port (Req 5.1, 5.5).

Time enters the domain only through an injected :class:`Clock`; domain code
never reads the wall clock directly (Req 5.5, ``.kiro/steering/tech.md``). This
makes every time-dependent behavior — verification/reset link expiry, session
duration, the OAuth link-confirmation window, and the rate-limit window —
deterministic in tests, where a controllable ``Test_Clock`` implements this same
port.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current time for the domain.

    Production uses a system-clock implementation; test and development use a
    controllable ``Test_Clock`` (added in a later task) whose time can be set or
    advanced. Both satisfy this protocol, so domain code reads time the same way
    regardless of environment.
    """

    def now(self) -> datetime:
        """Return the current time as a timezone-aware :class:`datetime`.

        The returned instant MUST be timezone-aware (UTC). Domain logic compares
        instants read through this method to decide expiry and window
        boundaries, so an implementation must never return a naive datetime.
        """
        ...
