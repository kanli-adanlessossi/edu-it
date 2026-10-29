"""Rules-based + property tests for hints (task 4.7, Req 7).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.hints`
does not exist yet (it lands in task 4.8). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists.

Intended public API of :mod:`edu_it_domain.learning.hints` (task 4.8)
---------------------------------------------------------------------
The service is pure, framework-free, and reads time only through the injected
``Clock`` (Req 1.1, 1.3), matching the other learning services in the design's
"Domain services" table ("Apply the Config hint multiplier when a hint was
revealed before the attempt was submitted; never retroactively"). The hint
multiplier is read from :mod:`edu_it_domain.config`
(:data:`~edu_it_domain.config.HINT_MULTIPLIER`, 0.5), never hardcoded (Req 7.3,
``.kiro/steering/quality.md``). These tests target this surface:

``tempered_gain(base_gain, *, hint_revealed_before_submission, multiplier=HINT_MULTIPLIER) -> int``
    The pure tempering rule (Req 7.2, 7.4). Given the ``base_gain`` an attempt
    would earn with no hint and whether a hint for that attempt's question was
    revealed **before** the attempt was submitted, return the mastery gain that
    attempt actually earns:

    - no pre-submission reveal → ``base_gain`` unchanged (Req 7.4);
    - a pre-submission reveal → ``base_gain`` tempered by ``multiplier`` (Req 7.2).

    The gain is an integer mastery delta, so the tempered value is the integer
    result of scaling the base gain by the multiplier. **Pinned rounding
    convention (the contract task 4.8 must satisfy):** the tempered gain is
    ``int(base_gain * multiplier)`` — truncation toward zero — which maps the
    worked example's ``+18`` no-hint gain to the ``+9`` hint-tempered gain
    (``int(18 * 0.5) == 9``), matching ``docs/RULES-REVIEW.md`` D7 §9. The
    multiplier is never applied retroactively: it tempers only an attempt whose
    hint was revealed before submission.

``HintReveal`` (frozen record) with a ``record_hint_reveal`` producer
    ``record_hint_reveal(mastery, *, clock, base_gain, source=HINT_SOURCE) ->
    tuple[HintReveal, Mastery]`` records that a learner revealed a hint for an
    attempt and applies the tempered gain to the learner's mastery. It returns
    exactly one :class:`~edu_it_domain.learning.hints.HintReveal` (the recorded
    ``hint_reveal``, stamped at the injected ``Clock`` instant) and a new
    :class:`~edu_it_domain.learning.models.Mastery` carrying **exactly one**
    additional :class:`~edu_it_domain.learning.models.MasteryEvent` for the
    tempered gain (Req 7.1). The reveal is a pre-submission reveal, so the
    applied gain is ``tempered_gain(base_gain,
    hint_revealed_before_submission=True)``.

These tests drive the real behavior against the infrastructure fakes — the
controllable :class:`Test_Clock` — wired exactly as the other fake-driven tests
wire it: no database and no wall clock (Req 11.4). The multiplier and the
mastery bounds come from :mod:`edu_it_domain.config`, never hardcoded (Req 7.3).

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

from datetime import UTC, datetime

from edu_it_domain.config import HINT_MULTIPLIER, MASTERY_MAX
from edu_it_domain.learning.hints import (
    HintReveal,
    record_hint_reveal,
    tempered_gain,
)
from edu_it_domain.learning.models import (
    Mastery,
    MasteryState,
    StableId,
)
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_USER: StableId = "usr_1"
_CONCEPT: StableId = "cn_1"
_START = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

#: The illustrative no-hint gain from the worked mastery example (D7 §9): a
#: correct quiz answer is +18, tempered to +9 when a hint was revealed first.
_BASE_GAIN = 18


def _mastery(
    score: int,
    state: MasteryState = MasteryState.PRACTICED,
    *,
    concept_id: StableId = _CONCEPT,
    user_id: StableId = _USER,
) -> Mastery:
    """Build a freshly-tracked :class:`Mastery` at ``score`` with no events."""
    return Mastery(user_id=user_id, concept_id=concept_id, score=score, state=state)


def _expected_tempered(base_gain: int) -> int:
    """The pinned tempered gain for ``base_gain`` under the hint multiplier.

    Computed the same way the implementation must round — ``int(base * mult)``
    (truncation toward zero) — reading the multiplier from Config rather than
    hardcoding it (Req 7.3).
    """
    return int(base_gain * HINT_MULTIPLIER)


# --- Generators constrained to the input space -------------------------------

#: Base (no-hint) gains spanning ordinary quiz gains and the 0 edge, kept within
#: the mastery scale so the gain is a plausible mastery delta.
_base_gains = st.integers(min_value=0, max_value=MASTERY_MAX)


# =============================================================================
# Example test — a hint reveal records one hint_reveal and one mastery_event
# (Req 7.1)
# =============================================================================


def test_hint_reveal_records_one_hint_reveal_and_one_mastery_event() -> None:
    # Req 7.1: WHEN a learner reveals a hint, exactly one hint_reveal is recorded
    # and exactly one corresponding mastery_event is appended for the associated
    # mastery change (D3 §7.4). The hint_reveal is stamped at the Clock instant.
    clock = Test_Clock(start=_START)
    before = _mastery(36, MasteryState.PRACTICED)  # D7 §9: at 36 before the hinted Q3

    reveal, after = record_hint_reveal(before, clock=clock, base_gain=_BASE_GAIN)

    # Exactly one hint_reveal record, stamped at the Clock instant.
    assert isinstance(reveal, HintReveal)
    assert reveal.revealed_at == clock.now()

    # Exactly one corresponding mastery_event is appended; history only grows.
    assert len(after.events) == len(before.events) + 1
    event = after.events[-1]
    assert event.occurred_at == clock.now()

    # The reveal is pre-submission, so the applied gain is the tempered gain
    # (the +9-vs-+18 temper of the worked example), not the full base gain.
    assert event.delta == _expected_tempered(_BASE_GAIN)
    assert after.score == before.score + _expected_tempered(_BASE_GAIN)


# =============================================================================
# Rules-based tests — pre-submission reveal tempers the gain (Req 7.2)
# =============================================================================


def test_pre_submission_reveal_tempers_the_gain() -> None:
    # Req 7.2: WHEN a hint for the attempt's question was revealed before the
    # attempt was submitted, the gain is tempered by the Config hint multiplier
    # relative to the no-hint answer (+9 vs +18 in the worked example, D7 §9).
    tempered = tempered_gain(_BASE_GAIN, hint_revealed_before_submission=True)
    no_hint = tempered_gain(_BASE_GAIN, hint_revealed_before_submission=False)

    assert no_hint == _BASE_GAIN
    assert tempered == _expected_tempered(_BASE_GAIN)
    # The temper is strictly smaller than the no-hint gain for a positive base.
    assert tempered < no_hint


def test_reads_the_multiplier_from_config() -> None:
    # Req 7.3: the hint multiplier is read from Config (domain/config.py), not
    # hardcoded. The default-argument temper equals the explicit-Config temper.
    with_default = tempered_gain(_BASE_GAIN, hint_revealed_before_submission=True)
    with_config = tempered_gain(
        _BASE_GAIN,
        hint_revealed_before_submission=True,
        multiplier=HINT_MULTIPLIER,
    )
    assert with_default == with_config == int(_BASE_GAIN * HINT_MULTIPLIER)


# =============================================================================
# Rules-based tests — post-submission reveal never tempers (Req 7.4)
# =============================================================================


def test_post_submission_reveal_leaves_the_gain_unchanged() -> None:
    # Req 7.4: WHEN a hint is revealed only after the attempt was submitted, the
    # multiplier is NOT applied to that already-submitted attempt's gain — the
    # gain is left exactly as the no-hint gain (never applied retroactively).
    assert tempered_gain(_BASE_GAIN, hint_revealed_before_submission=False) == _BASE_GAIN


def test_zero_base_gain_is_unchanged_either_way() -> None:
    # A zero base gain tempers to zero whether or not a hint was revealed first;
    # the multiplier scales a gain, it does not create one.
    assert tempered_gain(0, hint_revealed_before_submission=True) == 0
    assert tempered_gain(0, hint_revealed_before_submission=False) == 0


# =============================================================================
# Property 13: Hint multiplier applies only to pre-submission reveals
# (Req 7.2, 7.4)
# =============================================================================


@settings(max_examples=200)
@given(base_gain=_base_gains, revealed_before_submission=st.booleans())
def test_property_13_hint_multiplier_only_on_pre_submission_reveals(
    base_gain: int,
    revealed_before_submission: bool,
) -> None:
    """Feature: learning-core, Property 13: Hint multiplier applies only to pre-submission reveals.

    For any attempt, when a hint for its question was revealed before the attempt
    was submitted the computed mastery gain is tempered by the Config hint
    multiplier relative to the same answer with no hint, and a hint revealed only
    after submission leaves that attempt's gain unchanged (Req 7.2, 7.4).
    """
    gain = tempered_gain(
        base_gain,
        hint_revealed_before_submission=revealed_before_submission,
    )
    no_hint_gain = tempered_gain(base_gain, hint_revealed_before_submission=False)

    # A no-hint attempt (and, equivalently, a post-submission reveal) earns the
    # full base gain — the multiplier is never applied retroactively (Req 7.4).
    assert no_hint_gain == base_gain

    if revealed_before_submission:
        # Tempered relative to the no-hint gain by the Config multiplier (Req 7.2),
        # computed with the pinned integer-truncation rounding convention.
        assert gain == int(base_gain * HINT_MULTIPLIER)
        # For a positive base gain the temper is a strict reduction; it never
        # exceeds the no-hint gain and never goes negative.
        assert 0 <= gain <= no_hint_gain
        if base_gain > 0:
            assert gain < no_hint_gain
    else:
        # No pre-submission reveal: the gain is exactly the no-hint gain (Req 7.4).
        assert gain == base_gain
