"""The production system-clock ``Clock`` adapter (Req 5.1).

:class:`System_Clock` implements the domain
:class:`~edu_it_domain.ports.clock.Clock` port by reading the operating system's
wall clock. It is the production time source: domain and application code read
time through the injected ``Clock`` exactly as they do in test/dev, but here the
value tracks real time rather than a controllable :class:`Test_Clock`.

Unlike the ``Test_Clock`` fake, this adapter exposes **no** interface that sets
or advances the clock (Req 6.3). It carries a single read-only :meth:`now`, so
wiring it in the production composition root cannot register a clock-setting
surface; the set/advance capability exists only on the test/dev fake.
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_domain.ports.clock import Clock

__all__ = ["System_Clock"]


class System_Clock(Clock):
    """A :class:`Clock` that returns the current system time in UTC.

    Satisfies the :class:`~edu_it_domain.ports.clock.Clock` protocol with a
    single read-only :meth:`now`; it holds no mutable state and exposes no way to
    set or advance time (Req 6.3).
    """

    def now(self) -> datetime:
        """Return the current system time as a timezone-aware (UTC) datetime.

        The ``Clock`` port requires an aware UTC instant, so this reads the wall
        clock directly in UTC and never returns a naive datetime.
        """
        return datetime.now(UTC)
