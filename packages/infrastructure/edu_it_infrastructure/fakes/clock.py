"""The controllable ``Test_Clock`` fake (Req 6).

:class:`Test_Clock` implements the domain :class:`~edu_it_domain.ports.clock.Clock`
port with a time that a caller can ``set`` to an absolute instant or ``advance`` by
a non-negative duration, so time-dependent behavior (link expiry, session duration,
the OAuth link-confirmation window, the rate-limit window) is deterministic in tests
and local development.

Semantics (Req 6.1, 6.2, 6.4, 6.5):

- ``set(instant)`` makes ``instant`` the current time read through the injected
  ``Clock``; ``now()`` returns exactly that instant afterward (Req 6.1, 6.4).
- ``advance(duration)`` with a non-negative duration moves the current time forward
  by exactly that duration (Req 6.2, 6.4).
- A negative duration, or an instant that is not a valid timezone-aware
  ``datetime``, is rejected: the current time is left unchanged and an error is
  raised indicating the invalid input (Req 6.5).

This fake is enabled only in test and development environments; production code
registers no interface that sets or advances the clock (Req 6.3), which is enforced
by wiring this class only in the test/dev composition root.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.ports.clock import Clock

__all__ = ["Test_Clock"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""Default current time until a caller sets or advances the clock."""


def _require_valid_instant(instant: datetime) -> datetime:
    """Return ``instant`` as a UTC-aware datetime, or raise ``ValueError``.

    An instant is valid only when it is a timezone-aware :class:`datetime`
    (Req 6.5); a naive datetime or a non-datetime is rejected. The ``Clock``
    port requires ``now()`` to return a timezone-aware UTC datetime, so a valid
    aware instant is normalized to UTC.
    """
    if not isinstance(instant, datetime):
        raise ValueError(f"invalid instant: expected a datetime, got {type(instant).__name__}")
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        raise ValueError("invalid instant: expected a timezone-aware datetime")
    return instant.astimezone(UTC)


class Test_Clock(Clock):
    """A controllable :class:`Clock` whose current time can be set or advanced.

    Satisfies the :class:`~edu_it_domain.ports.clock.Clock` protocol, so domain
    code reads time through :meth:`now` exactly as it does in production.
    """

    # The design mandates the name ``Test_Clock`` (Req 6). The ``Test`` prefix
    # would otherwise make pytest try to collect this as a test class; this
    # marker tells pytest it is not a test container.
    __test__ = False

    def __init__(self, start: datetime | None = None) -> None:
        """Create a clock positioned at ``start`` (default: the Unix epoch, UTC).

        ``start``, when given, must be a valid timezone-aware instant; otherwise a
        ``ValueError`` is raised (Req 6.5).
        """
        self._now: datetime = _EPOCH if start is None else _require_valid_instant(start)

    def now(self) -> datetime:
        """Return the current time as a timezone-aware (UTC) datetime (Req 6.4)."""
        return self._now

    def set(self, instant: datetime) -> None:
        """Make ``instant`` the current time (Req 6.1, 6.4).

        After this call, :meth:`now` returns exactly ``instant`` (normalized to
        UTC). An instant that is not a valid timezone-aware datetime is rejected:
        the current time is left unchanged and a ``ValueError`` is raised (Req 6.5).
        """
        validated = _require_valid_instant(instant)
        self._now = validated

    def advance(self, duration: timedelta) -> None:
        """Move the current time forward by ``duration`` (Req 6.2, 6.4).

        ``duration`` must be a non-negative :class:`~datetime.timedelta`; after the
        call :meth:`now` equals the prior current time plus ``duration``. A negative
        duration, or a non-timedelta, is rejected: the current time is left
        unchanged and a ``ValueError`` is raised (Req 6.5).
        """
        if not isinstance(duration, timedelta):
            raise ValueError(
                f"invalid duration: expected a timedelta, got {type(duration).__name__}"
            )
        if duration < timedelta(0):
            raise ValueError("invalid duration: must be non-negative")
        self._now = self._now + duration
