"""Rules-based + property tests for anti-farming (task 4.5, Req 6).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.antifarming`
does not exist yet (it lands in task 4.6). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists. Do not weaken these tests to make them collect; they
turn green only once task 4.6 lands the service.

Intended public API of :mod:`edu_it_domain.learning.antifarming` (task 4.6)
---------------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Decide ``counted_for_mastery`` from the 24h window
(``first_answered_at + 24h``, Clock)"). The one business number it needs — the
24-hour window — is read from :data:`edu_it_domain.config.ANTI_FARMING_WINDOW`,
never hardcoded (Req 1.3, 6.4). These tests target this surface:

``counts_for_mastery(first_answered_at, *, clock, window=ANTI_FARMING_WINDOW) -> bool``
    The pure window predicate (Req 6.1, 6.4, 6.5). Returns ``False`` for a
    re-answer whose time (read from the injected ``Clock``) lies in the
    **half-open** interval ``[first_answered_at, first_answered_at + window)`` —
    the re-answer earns zero additional mastery — and ``True`` at or after the
    boundary ``first_answered_at + window``, where the standard mastery rules
    apply. The window is anchored at ``first_answered_at`` and measured by the
    Clock, never the wall clock (Req 6.4).

``evaluate_reanswer(question_id, first_answered_at, *, clock,
window=ANTI_FARMING_WINDOW) -> Answer``
    Produce the repeat :class:`~edu_it_domain.learning.models.Answer` for a
    re-answer evaluated at the injected ``Clock`` instant: the returned answer
    carries the same ``question_id`` and ``first_answered_at`` and sets
    ``counted_for_mastery`` to exactly ``counts_for_mastery(first_answered_at,
    clock=clock, window=window)`` — ``False`` inside the window (Req 6.1, 6.2),
    ``True`` at or after the boundary under the standard rules (Req 6.5).

The half-open boundary is the load-bearing detail (Req 6.1 vs 6.5): a re-answer
*at or after* ``first_answered_at`` and *strictly before* ``first_answered_at +
24h`` is inside the window (``counted_for_mastery`` false, zero gain); a
re-answer *exactly at* ``first_answered_at + 24h`` and later is outside
(``counted_for_mastery`` true, standard rules). The window length is read from
:data:`ANTI_FARMING_WINDOW`, so these tests never hardcode 24 hours.

These tests drive the real behavior against the controllable :class:`Test_Clock`
fake — a fixed UTC start instant advanced to position each re-answer relative to
the window — wired exactly as the other fake-driven tests wire it: no database
and no wall clock (Req 11.4).

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.config import ANTI_FARMING_WINDOW
from edu_it_domain.learning.antifarming import (
    counts_for_mastery,
    evaluate_reanswer,
)
from edu_it_domain.learning.models import Answer, StableId
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_QUESTION: StableId = "qn_1"

#: A fixed timezone-aware UTC start instant that anchors every re-answer. Using a
#: fixed instant keeps the window arithmetic deterministic and Clock-driven.
_FIRST_ANSWERED_AT = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _clock_at(instant: datetime) -> Test_Clock:
    """Return a :class:`Test_Clock` positioned exactly at ``instant``."""
    return Test_Clock(start=instant)


# --- Generators constrained to the input space -------------------------------

#: Offsets strictly inside the half-open window: at or after the anchor and
#: strictly before ``first_answered_at + ANTI_FARMING_WINDOW``. Bounded one
#: microsecond short of the boundary so the strict upper bound is respected and
#: the exact-boundary case is reserved for the dedicated edge tests below.
_inside_offsets = st.timedeltas(
    min_value=timedelta(0),
    max_value=ANTI_FARMING_WINDOW - timedelta(microseconds=1),
)

#: Offsets at or after the boundary: the exact ``+window`` instant and beyond,
#: up to well past it, where the standard rules apply.
_at_or_after_offsets = st.timedeltas(
    min_value=ANTI_FARMING_WINDOW,
    max_value=ANTI_FARMING_WINDOW + timedelta(days=365),
)

#: Arbitrary non-negative offsets spanning before/inside/at/after the boundary so
#: a single property exercises the whole decision surface at once.
_any_offsets = st.timedeltas(
    min_value=timedelta(0),
    max_value=ANTI_FARMING_WINDOW * 3,
)


# =============================================================================
# Rules-based tests — inside the window: zero gain, counted_for_mastery false
# (Req 6.1, 6.2)
# =============================================================================


def test_reanswer_just_inside_the_window_does_not_count() -> None:
    # Req 6.1 / 6.2: a re-answer strictly before first_answered_at + 24h earns
    # zero additional mastery and is recorded with counted_for_mastery=false. One
    # microsecond short of the boundary is still inside the half-open window.
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW - timedelta(microseconds=1))
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is False


def test_reanswer_midway_through_the_window_does_not_count() -> None:
    # Req 6.1 / 6.2: a re-answer halfway through the window is inside it, so it
    # earns zero additional mastery (counted_for_mastery=false).
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW / 2)
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is False


# =============================================================================
# Rules-based tests — the exact boundary edge cases (Req 6.1, 6.4, 6.5)
# =============================================================================


def test_reanswer_exactly_at_first_answered_at_does_not_count() -> None:
    # Req 6.1: the window is anchored at first_answered_at with an inclusive lower
    # bound — a re-answer exactly at first_answered_at is inside the window, so it
    # earns zero additional mastery (counted_for_mastery=false).
    clock = _clock_at(_FIRST_ANSWERED_AT)
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is False


def test_reanswer_exactly_at_the_24h_boundary_counts() -> None:
    # Req 6.4 / 6.5: the boundary is first_answered_at + ANTI_FARMING_WINDOW,
    # measured by the Clock, and it is the EXCLUSIVE upper bound of the window —
    # a re-answer exactly at the boundary is OUTSIDE the window and is evaluated
    # under the standard rules (counted_for_mastery=true). This is the exact
    # 24-hour boundary edge case the task calls out.
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW)
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is True


def test_reanswer_just_after_the_boundary_counts() -> None:
    # Req 6.5: just past the boundary the standard rules apply
    # (counted_for_mastery=true).
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW + timedelta(microseconds=1))
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is True


def test_reanswer_well_after_the_boundary_counts() -> None:
    # Req 6.5: long after the window, a re-answer is evaluated under the standard
    # mastery rules (counted_for_mastery=true).
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW + timedelta(days=30))
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is True


# =============================================================================
# Rules-based tests — evaluate_reanswer builds the recorded Answer (Req 6.2, 6.5)
# =============================================================================


def test_evaluate_reanswer_inside_window_records_counted_false() -> None:
    # Req 6.2: a re-answer inside the window records the repeat answer with
    # counted_for_mastery=false (no positive mastery delta is emitted for it).
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW - timedelta(hours=1))
    answer = evaluate_reanswer(_QUESTION, _FIRST_ANSWERED_AT, clock=clock)
    assert isinstance(answer, Answer)
    assert answer.question_id == _QUESTION
    assert answer.first_answered_at == _FIRST_ANSWERED_AT
    assert answer.counted_for_mastery is False


def test_evaluate_reanswer_at_boundary_records_counted_true() -> None:
    # Req 6.5: at or after the boundary the repeat answer is recorded with
    # counted_for_mastery=true, evaluated under the standard mastery rules. The
    # first_answered_at anchor is preserved (Req 6.4).
    clock = _clock_at(_FIRST_ANSWERED_AT + ANTI_FARMING_WINDOW)
    answer = evaluate_reanswer(_QUESTION, _FIRST_ANSWERED_AT, clock=clock)
    assert answer.question_id == _QUESTION
    assert answer.first_answered_at == _FIRST_ANSWERED_AT
    assert answer.counted_for_mastery is True


def test_evaluate_reanswer_matches_the_window_predicate() -> None:
    # evaluate_reanswer sets counted_for_mastery to exactly the window predicate,
    # so the two stay consistent across the boundary.
    for offset in (
        timedelta(0),
        ANTI_FARMING_WINDOW - timedelta(microseconds=1),
        ANTI_FARMING_WINDOW,
        ANTI_FARMING_WINDOW + timedelta(hours=1),
    ):
        clock = _clock_at(_FIRST_ANSWERED_AT + offset)
        answer = evaluate_reanswer(_QUESTION, _FIRST_ANSWERED_AT, clock=clock)
        assert answer.counted_for_mastery is counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock)


# =============================================================================
# Property 12: Re-answers inside the 24-hour window earn no additional mastery
# (Req 6.1, 6.2, 6.4, 6.5)
# =============================================================================


@settings(max_examples=200)
@given(anchor=st.datetimes().map(lambda n: n.replace(tzinfo=UTC)), offset=_any_offsets)
def test_property_12_half_open_window_decides_counted_for_mastery(
    anchor: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 12: Re-answers inside the 24-hour window
    earn no additional mastery.

    For any re-answer of the same question at or after ``first_answered_at`` and
    strictly before ``first_answered_at`` plus the Config window (measured by the
    Clock), the re-answer grants zero additional mastery and is recorded with
    ``counted_for_mastery`` false; at or after the window it is evaluated under
    the standard mastery rules with ``counted_for_mastery`` true (Req 6.1, 6.2,
    6.4, 6.5).
    """
    clock = _clock_at(anchor + offset)

    # The half-open window [anchor, anchor + ANTI_FARMING_WINDOW): inside → false,
    # at/after the boundary → true. The boundary is read from config, not 24h.
    inside_window = offset < ANTI_FARMING_WINDOW
    expected = not inside_window

    assert counts_for_mastery(anchor, clock=clock) is expected

    answer = evaluate_reanswer(_QUESTION, anchor, clock=clock)
    assert answer.counted_for_mastery is expected
    assert answer.question_id == _QUESTION
    assert answer.first_answered_at == anchor


@settings(max_examples=200)
@given(anchor=st.datetimes().map(lambda n: n.replace(tzinfo=UTC)), offset=_inside_offsets)
def test_property_12_inside_window_never_counts(
    anchor: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 12: Re-answers inside the 24-hour window
    earn no additional mastery.

    For any re-answer strictly inside the half-open window, the re-answer earns
    zero additional mastery and is recorded with ``counted_for_mastery`` false
    (Req 6.1, 6.2, 6.4).
    """
    clock = _clock_at(anchor + offset)
    assert counts_for_mastery(anchor, clock=clock) is False
    assert evaluate_reanswer(_QUESTION, anchor, clock=clock).counted_for_mastery is False


@settings(max_examples=200)
@given(anchor=st.datetimes().map(lambda n: n.replace(tzinfo=UTC)), offset=_at_or_after_offsets)
def test_property_12_at_or_after_boundary_always_counts(
    anchor: datetime,
    offset: timedelta,
) -> None:
    """Feature: learning-core, Property 12: Re-answers inside the 24-hour window
    earn no additional mastery.

    For any re-answer at or after ``first_answered_at`` plus the Config window,
    the answer is evaluated under the standard mastery rules with
    ``counted_for_mastery`` true (Req 6.5).
    """
    clock = _clock_at(anchor + offset)
    assert counts_for_mastery(anchor, clock=clock) is True
    assert evaluate_reanswer(_QUESTION, anchor, clock=clock).counted_for_mastery is True


# =============================================================================
# Edge-case property — the exact boundary is the exclusive upper bound
# (Req 6.1 vs 6.5)
# =============================================================================


@settings(max_examples=200)
@given(anchor=st.datetimes().map(lambda n: n.replace(tzinfo=UTC)))
def test_property_12_exact_boundary_counts_one_microsecond_before_does_not(
    anchor: datetime,
) -> None:
    """Feature: learning-core, Property 12: Re-answers inside the 24-hour window
    earn no additional mastery.

    For any anchor, the instant exactly at ``first_answered_at`` plus the Config
    window counts (``counted_for_mastery`` true) while the instant one microsecond
    earlier does not, pinning the half-open window's exclusive upper bound
    (Req 6.1, 6.4, 6.5).
    """
    boundary = anchor + ANTI_FARMING_WINDOW

    assert counts_for_mastery(anchor, clock=_clock_at(boundary)) is True
    assert (
        counts_for_mastery(anchor, clock=_clock_at(boundary - timedelta(microseconds=1))) is False
    )


@pytest.mark.parametrize(
    ("offset", "expected_counted"),
    [
        (timedelta(0), False),  # exactly at first_answered_at — inside (Req 6.1)
        (ANTI_FARMING_WINDOW / 2, False),  # midway — inside
        (ANTI_FARMING_WINDOW - timedelta(microseconds=1), False),  # just inside the boundary
        (ANTI_FARMING_WINDOW, True),  # exactly at the boundary — outside (Req 6.5)
        (ANTI_FARMING_WINDOW + timedelta(microseconds=1), True),  # just after the boundary
    ],
)
def test_window_boundary_table_is_half_open(
    offset: timedelta,
    expected_counted: bool,
) -> None:
    # The written window is the half-open interval
    # [first_answered_at, first_answered_at + ANTI_FARMING_WINDOW): inclusive
    # lower bound, exclusive upper bound. The offsets are expressed relative to
    # ANTI_FARMING_WINDOW so a config change flows through (Req 6.4) rather than
    # being hardcoded to 24 hours.
    clock = _clock_at(_FIRST_ANSWERED_AT + offset)
    assert counts_for_mastery(_FIRST_ANSWERED_AT, clock=clock) is expected_counted
