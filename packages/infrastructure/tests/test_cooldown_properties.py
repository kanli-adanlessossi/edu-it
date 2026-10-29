"""Rules-based + property tests for cooldown (task 4.3, Req 4.1, 4.2, 4.3).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.cooldown`
does not exist yet (it lands in task 4.4). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.cooldown` (task 4.4)
------------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.2), matching the other learning services in the design's
"Domain services" table ("Report cooldown state from last-attempt time + Clock +
Config interval"). The cooldown interval is read from
:data:`edu_it_domain.config.COOLDOWN_INTERVAL`, never hardcoded (Req 1.3, 4.3);
it is exposed as an optional keyword argument defaulting to that config value so
a caller can override it — mirroring the ``window`` parameter on the
``Redis_SignInRateLimiter`` adapter, whose default is the domain business
constant and whose override exists only to exercise small bounds in tests. These
tests target this surface:

``is_in_cooldown(last_attempt_at, *, clock, interval=COOLDOWN_INTERVAL) -> bool``
    The pure cooldown predicate (Req 4.1, 4.2). Returns ``True`` — the quiz is
    reported in cooldown — if and only if the Clock reading is **strictly
    before** ``last_attempt_at + interval``. At exactly ``last_attempt_at +
    interval`` (and after) the quiz is attemptable again, so the predicate
    returns ``False`` at the boundary (Req 4.2). ``last_attempt_at`` and the
    Clock reading are timezone-aware UTC datetimes; ``interval`` defaults to
    :data:`~edu_it_domain.config.COOLDOWN_INTERVAL`.

``attemptable_at(last_attempt_at, *, interval=COOLDOWN_INTERVAL) -> datetime``
    The instant at which the quiz becomes attemptable again — exactly
    ``last_attempt_at + interval``. It needs no Clock because it is the boundary
    itself; :func:`is_in_cooldown` is ``clock.now() < attemptable_at(...)``. This
    is the instant a cooldown-rejection problem detail reports as "attemptable
    at" (design's quiz-attempt sequence, Req 24.2) and is asserted here to agree
    with the predicate at, just before, and just after the boundary.

These tests drive time through the controllable :class:`Test_Clock` fake wired
exactly as the other fake-driven tests wire it — a fixed UTC start instant that
is ``set``/``advance``-d across the boundary — with no database and no wall clock
(Req 11.4). The interval is always read from
:data:`~edu_it_domain.config.COOLDOWN_INTERVAL` (or an explicit override passed
through the same keyword), never a hardcoded duration (Req 4.3).

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import COOLDOWN_INTERVAL
from edu_it_domain.learning.cooldown import attemptable_at, is_in_cooldown
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants ---------------------------------------------------------

#: A fixed UTC start instant for the controllable clock. Every rules test places
#: the last attempt here and moves the clock relative to it so the boundary is
#: ``_START + COOLDOWN_INTERVAL``.
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# --- Generators constrained to the input space --------------------------------

#: Last-attempt instants kept well inside the datetime range so that adding
#: ``COOLDOWN_INTERVAL`` (and the clock offsets below) never overflows.
_last_attempts = st.datetimes(
    min_value=datetime(2000, 1, 1, 0, 0, 0),
    max_value=datetime(2100, 1, 1, 0, 0, 0),
).map(lambda naive: naive.replace(tzinfo=UTC))

#: Offsets of the Clock reading relative to the last-attempt instant, spanning
#: well before the boundary, the sub-interval region, and well past it. The
#: exact boundary (offset == COOLDOWN_INTERVAL) and its immediate neighbours are
#: covered by the dedicated boundary tests below; here the offsets exercise the
#: iff relationship broadly on both sides.
_clock_offsets = st.timedeltas(
    min_value=timedelta(0),
    max_value=timedelta(days=2),
)

#: Custom cooldown intervals for the override path (Req 4.3): a caller may pass
#: an interval other than the config default. Strictly positive so the boundary
#: is meaningful.
_intervals = st.timedeltas(
    min_value=timedelta(seconds=1),
    max_value=timedelta(days=7),
)


# =============================================================================
# Rules-based tests — in cooldown before the interval elapses (Req 4.1)
# =============================================================================


def test_in_cooldown_immediately_after_the_last_attempt() -> None:
    # Req 4.1: WHILE the cooldown interval since the last attempt has not elapsed,
    # the quiz is reported in cooldown. At the last-attempt instant itself, the
    # full interval is still ahead, so the quiz is in cooldown.
    clock = Test_Clock(start=_START)
    assert is_in_cooldown(_START, clock=clock) is True


def test_in_cooldown_just_before_the_boundary() -> None:
    # Req 4.1: one second before the interval elapses the quiz is still in
    # cooldown. The boundary is read from config, never hardcoded.
    clock = Test_Clock(start=_START)
    clock.set(_START + COOLDOWN_INTERVAL - timedelta(seconds=1))
    assert is_in_cooldown(_START, clock=clock) is True


# =============================================================================
# Rules-based tests — attemptable at and after the boundary (Req 4.2)
# =============================================================================


def test_attemptable_exactly_at_the_boundary() -> None:
    # Req 4.2: WHEN the cooldown interval has elapsed, the quiz is attemptable.
    # The predicate is "strictly before" the boundary, so *exactly at*
    # ``last_attempt + COOLDOWN_INTERVAL`` the quiz is already attemptable.
    clock = Test_Clock(start=_START)
    clock.set(_START + COOLDOWN_INTERVAL)
    assert is_in_cooldown(_START, clock=clock) is False


def test_attemptable_just_after_the_boundary() -> None:
    # Req 4.2: one second past the boundary the quiz is attemptable again.
    clock = Test_Clock(start=_START)
    clock.set(_START + COOLDOWN_INTERVAL + timedelta(seconds=1))
    assert is_in_cooldown(_START, clock=clock) is False


def test_attemptable_long_after_the_boundary() -> None:
    # Req 4.2: well past the interval the quiz remains attemptable (the cooldown
    # does not somehow re-engage once elapsed).
    clock = Test_Clock(start=_START)
    clock.advance(COOLDOWN_INTERVAL + timedelta(days=30))
    assert is_in_cooldown(_START, clock=clock) is False


# =============================================================================
# Rules-based tests — interval read from Config, override honored (Req 4.3)
# =============================================================================


def test_boundary_tracks_the_config_interval() -> None:
    # Req 4.3: the interval is read from Config (COOLDOWN_INTERVAL). The
    # attemptable-at instant is exactly the last attempt plus that config value,
    # and the predicate flips there — asserted without hardcoding a duration.
    clock = Test_Clock(start=_START)
    assert attemptable_at(_START) == _START + COOLDOWN_INTERVAL

    # Just inside the config interval → in cooldown; exactly at it → attemptable.
    clock.set(attemptable_at(_START) - timedelta(microseconds=1))
    assert is_in_cooldown(_START, clock=clock) is True
    clock.set(attemptable_at(_START))
    assert is_in_cooldown(_START, clock=clock) is False


def test_custom_interval_override_shifts_the_boundary() -> None:
    # Req 4.3: a caller may override the interval (mirroring the rate limiter's
    # ``window`` parameter). With a custom interval the boundary moves to
    # ``last_attempt + custom`` and the predicate tracks it, independent of the
    # config default.
    custom = timedelta(minutes=5)
    clock = Test_Clock(start=_START)

    assert attemptable_at(_START, interval=custom) == _START + custom

    clock.set(_START + custom - timedelta(seconds=1))
    assert is_in_cooldown(_START, clock=clock, interval=custom) is True

    clock.set(_START + custom)
    assert is_in_cooldown(_START, clock=clock, interval=custom) is False


# =============================================================================
# Edge-case rules tests — the exact boundary triple (just before / at / after)
# =============================================================================


@pytest.mark.parametrize(
    ("offset", "expected_in_cooldown"),
    [
        (COOLDOWN_INTERVAL - timedelta(microseconds=1), True),  # just before
        (COOLDOWN_INTERVAL, False),  # exactly at the boundary → attemptable
        (COOLDOWN_INTERVAL + timedelta(microseconds=1), False),  # just after
    ],
)
def test_exact_boundary_is_strictly_before(
    offset: timedelta,
    expected_in_cooldown: bool,
) -> None:
    # The cooldown holds iff ``clock.now() < last_attempt + COOLDOWN_INTERVAL``
    # (strictly before). This pins the exact microsecond boundary: in cooldown
    # just before, attemptable exactly at and just after.
    clock = Test_Clock(start=_START)
    clock.set(_START + offset)
    assert is_in_cooldown(_START, clock=clock) is expected_in_cooldown


# =============================================================================
# Property 6: Cooldown holds exactly until the interval elapses (Req 4.1, 4.2)
# =============================================================================


@settings(max_examples=200)
@given(last_attempt_at=_last_attempts, offset=_clock_offsets)
def test_property_6_cooldown_holds_until_the_config_interval_elapses(
    last_attempt_at: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 6: Cooldown holds exactly until the interval elapses.

    For any last-attempt instant and any Clock reading, the quiz is reported in
    cooldown if and only if the Clock reading is strictly before the last-attempt
    instant plus the Config cooldown interval (Req 4.1, 4.2).
    """
    clock = Test_Clock(start=last_attempt_at + offset)

    boundary = last_attempt_at + COOLDOWN_INTERVAL
    # The iff relationship, with the boundary read from config (Req 4.3).
    expected = clock.now() < boundary
    assert is_in_cooldown(last_attempt_at, clock=clock) is expected

    # The predicate agrees with the exposed boundary instant.
    assert attemptable_at(last_attempt_at) == boundary
    assert is_in_cooldown(last_attempt_at, clock=clock) is (
        clock.now() < attemptable_at(last_attempt_at)
    )


@settings(max_examples=200)
@given(last_attempt_at=_last_attempts, offset=_clock_offsets, interval=_intervals)
def test_property_6_iff_holds_for_any_interval(
    last_attempt_at: datetime,
    offset: timedelta,
    interval: timedelta,
) -> None:
    """Feature: learning-core, Property 6: Cooldown holds exactly until the interval elapses.

    The strict-before iff relationship holds for any (overridable) cooldown
    interval, not only the config default: the quiz is in cooldown iff the Clock
    reading is strictly before ``last_attempt_at + interval`` (Req 4.1, 4.2, 4.3).
    """
    clock = Test_Clock(start=last_attempt_at + offset)

    boundary = last_attempt_at + interval
    expected = clock.now() < boundary
    assert is_in_cooldown(last_attempt_at, clock=clock, interval=interval) is expected
    assert attemptable_at(last_attempt_at, interval=interval) == boundary
