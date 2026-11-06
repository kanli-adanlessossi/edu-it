"""Termination grace arithmetic (Requirements 14.2, 14.4, 14.12).

Pure: no I/O, no clock, no framework. The Chart validation (task 12.11) and the
chart tests reuse these functions so the rule exists once.

``terminationGracePeriodSeconds`` must be at least the drain period plus the
preStop delay plus a fixed margin.
"""

from __future__ import annotations

from edu_it_domain import config as _config

__all__ = [
    "TerminationGraceError",
    "check_termination_grace",
    "min_termination_grace",
]


class TerminationGraceError(ValueError):
    """The termination grace period is below the required minimum.

    The message names the three values: drain, preStop delay and grace.
    """

    def __init__(self, drain: int, prestop: int, grace: int) -> None:
        self.drain = drain
        self.prestop = prestop
        self.grace = grace
        self.minimum = min_termination_grace(drain, prestop)
        super().__init__(
            f"terminationGracePeriodSeconds={grace} is below the minimum "
            f"{self.minimum} (drain_period_seconds={drain} + "
            f"preStop_delay_seconds={prestop} + "
            f"{_config.API_TERMINATION_GRACE_MARGIN_SECONDS})"
        )


def min_termination_grace(drain: int, prestop: int) -> int:
    """Return ``drain + prestop + 10`` (all in seconds)."""
    return drain + prestop + _config.API_TERMINATION_GRACE_MARGIN_SECONDS


def check_termination_grace(drain: int, prestop: int, grace: int) -> None:
    """Accept ``grace`` when it is at least the minimum, else raise.

    Raises:
        TerminationGraceError: naming ``drain``, ``prestop`` and ``grace``.
    """
    if grace < min_termination_grace(drain, prestop):
        raise TerminationGraceError(drain, prestop, grace)
