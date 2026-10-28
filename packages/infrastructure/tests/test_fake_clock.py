"""Unit tests for the controllable Test_Clock fake (task 5.1, Req 6.1, 6.2, 6.4, 6.5).

Example and edge-case coverage for the ``Test_Clock``: it satisfies the domain
``Clock`` port, ``set`` makes an instant the current time read back exactly,
``advance`` moves the current time forward by exactly a non-negative duration, and
invalid inputs (negative duration, naive/non-datetime instant) are rejected while
leaving the current time unchanged. The universal set/advance read-back property is
covered separately by the property-based test in task 5.2 (Property 36).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from edu_it_domain.ports import Clock
from edu_it_infrastructure.fakes import Test_Clock

_UTC = UTC


def test_test_clock_satisfies_the_domain_clock_port() -> None:
    # Substitutable for the production Clock without changing domain code (Req 5.2).
    assert isinstance(Test_Clock(), Clock)


def test_now_is_timezone_aware_utc() -> None:
    # The Clock port requires a timezone-aware UTC instant (Req 6.4).
    now = Test_Clock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_set_makes_instant_the_current_time_read_back_exactly() -> None:
    # Req 6.1, 6.4: time read through the Clock equals the set instant exactly.
    clock = Test_Clock()
    instant = datetime(2024, 3, 1, 9, 30, 15, tzinfo=_UTC)
    clock.set(instant)
    assert clock.now() == instant


def test_set_normalizes_a_non_utc_aware_instant_to_the_same_instant() -> None:
    # A non-UTC aware instant is accepted and compares equal to its UTC form.
    clock = Test_Clock()
    eastern = timezone(timedelta(hours=-5))
    instant = datetime(2024, 3, 1, 4, 30, 15, tzinfo=eastern)
    clock.set(instant)
    assert clock.now() == instant
    assert clock.now().utcoffset() == timedelta(0)


def test_advance_moves_current_time_forward_by_exactly_the_duration() -> None:
    # Req 6.2, 6.4: now() == prior now() + duration.
    start = datetime(2024, 1, 1, tzinfo=_UTC)
    clock = Test_Clock(start=start)
    clock.advance(timedelta(hours=24))
    assert clock.now() == start + timedelta(hours=24)


def test_advance_by_zero_leaves_current_time_unchanged() -> None:
    start = datetime(2024, 1, 1, tzinfo=_UTC)
    clock = Test_Clock(start=start)
    clock.advance(timedelta(0))
    assert clock.now() == start


def test_advance_accumulates_across_multiple_calls() -> None:
    start = datetime(2024, 1, 1, tzinfo=_UTC)
    clock = Test_Clock(start=start)
    clock.advance(timedelta(minutes=10))
    clock.advance(timedelta(minutes=5))
    assert clock.now() == start + timedelta(minutes=15)


def test_advance_by_negative_duration_is_rejected_and_leaves_time_unchanged() -> None:
    # Req 6.5: reject, leave current time unchanged, raise an error.
    start = datetime(2024, 1, 1, tzinfo=_UTC)
    clock = Test_Clock(start=start)
    with pytest.raises(ValueError, match="non-negative"):
        clock.advance(timedelta(seconds=-1))
    assert clock.now() == start


def test_set_rejects_a_naive_instant_and_leaves_time_unchanged() -> None:
    # Req 6.5: a naive datetime is not a valid instant.
    start = datetime(2024, 1, 1, tzinfo=_UTC)
    clock = Test_Clock(start=start)
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2024, 6, 1, 12, 0, 0))  # intentionally naive
    assert clock.now() == start


def test_set_rejects_a_non_datetime_instant() -> None:
    clock = Test_Clock()
    with pytest.raises(ValueError, match="datetime"):
        clock.set("2024-01-01T00:00:00Z")  # type: ignore[arg-type]


def test_advance_rejects_a_non_timedelta_duration() -> None:
    clock = Test_Clock()
    with pytest.raises(ValueError, match="timedelta"):
        clock.advance(3600)  # type: ignore[arg-type]


def test_constructor_rejects_an_invalid_start_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Test_Clock(start=datetime(2024, 1, 1))  # intentionally naive
