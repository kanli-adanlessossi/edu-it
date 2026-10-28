"""Property-based test for the Test_Clock set/advance read-back (task 5.2).

Feature: foundations, Property 36: Test_Clock set and advance read back exactly

Property 36 (design.md): *For any* instant ``t`` and any non-negative duration
``d``, after setting the Test_Clock to ``t`` the time read through the injected
Clock equals ``t``, and after advancing by ``d`` it equals ``t + d``.

This is the universal read-back property; specific examples, the rejection of
invalid inputs (Req 6.5), and the Clock-port conformance are covered by the
example/edge-case unit tests in ``test_fake_clock.py``.

Validates: Requirements 6.1, 6.2, 6.4, 6.5

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

# A valid instant for the Clock is a timezone-aware datetime (Req 6.4/6.5). Draw
# aware datetimes across a range of fixed UTC offsets (-14h..+14h, the real-world
# span of named zones) using ``datetime.timezone`` so the generator needs no
# external IANA tz database; set() normalizes to UTC, so the read-back is
# compared against the same normalization.
_fixed_offset_zones = st.builds(
    timezone,
    st.timedeltas(min_value=timedelta(hours=-14), max_value=timedelta(hours=14)),
)
_aware_instants = st.datetimes(timezones=_fixed_offset_zones)

# A non-negative duration (Req 6.2/6.5): zero up to ~10 years, at microsecond
# resolution, which is the span the clock is exercised over in tests.
_non_negative_durations = st.timedeltas(
    min_value=timedelta(0),
    max_value=timedelta(days=3650),
)


@given(instant=_aware_instants, duration=_non_negative_durations)
@settings(max_examples=200)
def test_property_36_set_and_advance_read_back_exactly(
    instant: datetime,
    duration: timedelta,
) -> None:
    """Feature: foundations, Property 36: Test_Clock set and advance read back exactly.

    After ``set(instant)`` the injected Clock reads back exactly ``instant``
    (normalized to UTC), and after ``advance(duration)`` with a non-negative
    duration it reads back exactly the prior time plus ``duration``.
    """
    clock = Test_Clock()

    # Req 6.1, 6.4: set() makes the instant the current time, read back exactly.
    clock.set(instant)
    expected_after_set = instant.astimezone(UTC)
    assert clock.now() == expected_after_set

    # Req 6.2, 6.4: advance() by a non-negative duration moves the current time
    # forward by exactly that duration (prior time + duration).
    clock.advance(duration)
    assert clock.now() == expected_after_set + duration
