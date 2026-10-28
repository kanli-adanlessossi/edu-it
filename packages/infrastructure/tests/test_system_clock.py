"""Unit tests for the production System_Clock adapter (task 7.1, Req 5.1, 6.3).

Example and edge-case coverage: the adapter satisfies the domain ``Clock`` port,
``now`` returns a timezone-aware UTC instant tracking real time, and the adapter
exposes no clock-setting surface (``set``/``advance``), so wiring it in
production registers no interface that sets or advances the clock (Req 6.3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edu_it_domain.ports import Clock
from edu_it_infrastructure.clock import System_Clock


def test_system_clock_satisfies_the_domain_clock_port() -> None:
    # Substitutable for the Test_Clock fake without changing domain code (Req 5.2).
    assert isinstance(System_Clock(), Clock)


def test_now_returns_a_timezone_aware_utc_instant() -> None:
    # Req 5.1 / Clock port: now() must be timezone-aware UTC, never naive.
    instant = System_Clock().now()
    assert instant.tzinfo is not None
    assert instant.utcoffset() == timedelta(0)


def test_now_tracks_real_time_within_a_tolerance() -> None:
    # The production clock reads the wall clock, so it is close to datetime.now.
    before = datetime.now(UTC)
    instant = System_Clock().now()
    after = datetime.now(UTC)
    assert before <= instant <= after


def test_now_is_monotonic_non_decreasing_across_reads() -> None:
    clock = System_Clock()
    first = clock.now()
    second = clock.now()
    assert second >= first


def test_system_clock_exposes_no_clock_setting_surface() -> None:
    # Req 6.3: the production clock carries no set/advance interface; only the
    # read-only now() is present. Wiring it cannot register a clock-setting API.
    clock = System_Clock()
    assert not hasattr(clock, "set")
    assert not hasattr(clock, "advance")
