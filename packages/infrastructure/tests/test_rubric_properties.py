"""Rules-based + property tests for the rubric service (task 6.5, Req 10).

These tests are authored **from the rules before** the implementation they
exercise (Req 11.1): the domain service :mod:`edu_it_domain.learning.rubric`
does not exist yet (it lands in task 6.6). Until then every test here fails to
import, which is the correct state for the rules-first ordering — the tests pin
the intended behavior the implementation must satisfy rather than describing
code that already exists. Do not add ``rubric.py`` to make these collect; the
import error is the expected signal that 6.6 is still outstanding.

Intended public API of :mod:`edu_it_domain.learning.rubric` (task 6.6)
----------------------------------------------------------------------
The service is pure, framework-free, and reads its business numbers from
:mod:`edu_it_domain.config` — never hardcoding the 0.7 pass fraction (Req 1.3;
``.kiro/steering/quality.md``). It matches the design's "Domain services" row:
"Evaluate weighted criteria vs ``max_points``; pass/fail at Config threshold;
outcome only after ``ProjectReviewer`` completes" (Req 10). These tests target
this surface:

``weighted_score(rubric, scores) -> int``
    The weighted total of a submission: the sum of the points awarded for each
    of ``rubric``'s criteria, where each criterion's contribution is its awarded
    points clamped to the inclusive ``0..criterion.max_points`` range (Req 10.1,
    10.2). ``scores`` is the collection of
    :class:`~edu_it_domain.learning.models.RubricScore` the reviewer produced
    (one per criterion, keyed by ``criterion_id``); a criterion with no matching
    score contributes 0. The result is the integer weighted sum the outcome is
    decided against, itself bounded by the rubric's total ``max_points``.

``total_max_points(rubric) -> int``
    The sum of every criterion's ``max_points`` — the denominator the Config
    pass fraction is taken of (Req 10.2).

``is_passing(rubric, scores) -> bool``
    Whether the weighted score meets or exceeds the pass threshold: passing iff
    ``weighted_score(rubric, scores) >= RUBRIC_PASS_THRESHOLD *
    total_max_points(rubric)`` (Req 10.2, 10.3). The comparison is ``>=`` so a
    score landing exactly on the threshold fraction passes; the threshold is
    read from :data:`~edu_it_domain.config.RUBRIC_PASS_THRESHOLD`, never
    hardcoded.

``evaluate(submission, rubric, reviewer) -> str | None``
    The end-to-end outcome of a submission routed through the
    :class:`~edu_it_domain.ports.project_reviewer.ProjectReviewer` extension
    point (Req 10.4). While the reviewer's evaluation is pending it returns
    ``None`` — the reviewer yields no scores and the domain records no
    ``review_outcome`` (Req 10.5). Once the reviewer has completed, it computes
    the weighted score from the reviewer's per-criterion scores and returns a
    passing outcome iff :func:`is_passing` holds, else a not-passing outcome
    (Req 10.2, 10.3). The two non-``None`` outcomes are distinguished by
    :func:`is_passing`; the tests assert only the pass/fail *distinction* and
    the pending-``None`` contract, not the particular string spelling.

These tests drive the real behavior against the infrastructure fake — the
configurable :class:`Fake_ProjectReviewer`, which yields per-criterion scores
when complete and ``None`` while pending — wired exactly as the other
fake-driven tests wire their ports: no database and no wall clock (Req 11.4).
They use the real
:class:`~edu_it_domain.learning.models.Rubric`,
:class:`~edu_it_domain.learning.models.RubricCriterion`,
:class:`~edu_it_domain.learning.models.RubricScore`, and
:class:`~edu_it_domain.learning.models.ProjectSubmissionRef` domain types, never
invented fields.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), at a minimum of 100 iterations per property.
"""

from __future__ import annotations

import math

from edu_it_domain.config import RUBRIC_PASS_THRESHOLD
from edu_it_domain.learning.models import (
    ProjectSubmissionRef,
    Rubric,
    RubricCriterion,
    RubricScore,
    StableId,
)
from edu_it_domain.learning.rubric import (
    evaluate,
    is_passing,
    total_max_points,
    weighted_score,
)
from edu_it_infrastructure.fakes import Fake_ProjectReviewer
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Shared constants and helpers --------------------------------------------

_PROJECT: StableId = "prj_1"
_RUBRIC: StableId = "rub_1"
_SUBMISSION: StableId = "sub_1"


def _criterion(index: int, max_points: int) -> RubricCriterion:
    """Build a rubric criterion with a stable per-rubric id and ``max_points``."""
    return RubricCriterion(stable_id=f"crit_{index}", max_points=max_points)


def _rubric(max_points: list[int]) -> Rubric:
    """Build a :class:`Rubric` whose criteria carry the given ``max_points``."""
    criteria = tuple(_criterion(i, mp) for i, mp in enumerate(max_points))
    return Rubric(stable_id=_RUBRIC, project_id=_PROJECT, criteria=criteria)


def _scores(rubric: Rubric, awarded: list[int]) -> tuple[RubricScore, ...]:
    """Pair each criterion of ``rubric`` with its awarded points, in order."""
    return tuple(
        RubricScore(criterion_id=criterion.stable_id, points=points)
        for criterion, points in zip(rubric.criteria, awarded, strict=True)
    )


def _threshold_points(rubric: Rubric) -> float:
    """The exact pass threshold in points: the Config fraction of ``max_points``.

    Read from :data:`RUBRIC_PASS_THRESHOLD` (never the literal 0.7) so a config
    change flows through rather than being baked into the test (Req 1.3).
    """
    return RUBRIC_PASS_THRESHOLD * total_max_points(rubric)


# --- Generators constrained to the input space -------------------------------

#: Per-criterion maximum points: positive so a rubric has a non-zero scale and
#: an exact threshold boundary exists to probe.
_max_points = st.integers(min_value=1, max_value=50)

#: A rubric's criterion max-points list: at least one criterion, up to a handful.
_rubric_shapes = st.lists(_max_points, min_size=1, max_size=8)


# =============================================================================
# Rules-based tests — the weighted score (Req 10.1, 10.2)
# =============================================================================


def test_weighted_score_sums_awarded_points_across_criteria() -> None:
    # Req 10.1, 10.2: the weighted score is the sum of the points awarded for
    # each criterion of the rubric.
    rubric = _rubric([10, 20, 30])
    scores = _scores(rubric, [5, 15, 25])
    assert weighted_score(rubric, scores) == 5 + 15 + 25


def test_weighted_score_clamps_each_criterion_to_its_max_points() -> None:
    # Req 10.2: each criterion's contribution is bounded by its own max_points —
    # an over-max award cannot inflate the weighted total beyond the rubric scale.
    rubric = _rubric([10, 20])
    over_max = _scores(rubric, [999, 25])
    assert weighted_score(rubric, over_max) == 10 + 20


def test_weighted_score_floors_a_negative_award_at_zero() -> None:
    # Req 10.2: an awarded value below 0 contributes 0, not a negative amount, so
    # one criterion can never pull the weighted total below the others' sum.
    rubric = _rubric([10, 20])
    negative = _scores(rubric, [-5, 20])
    assert weighted_score(rubric, negative) == 0 + 20


def test_weighted_score_treats_a_missing_criterion_score_as_zero() -> None:
    # A criterion with no matching RubricScore contributes 0 (Req 10.1).
    rubric = _rubric([10, 20, 30])
    only_first = (RubricScore(criterion_id=rubric.criteria[0].stable_id, points=7),)
    assert weighted_score(rubric, only_first) == 7


def test_all_max_awards_total_the_rubric_max_points() -> None:
    # The all-max submission scores exactly the rubric's total max_points.
    rubric = _rubric([10, 20, 30])
    full = _scores(rubric, [10, 20, 30])
    assert weighted_score(rubric, full) == total_max_points(rubric) == 60


def test_zero_awards_total_zero() -> None:
    # The all-zero submission scores 0.
    rubric = _rubric([10, 20, 30])
    none_awarded = _scores(rubric, [0, 0, 0])
    assert weighted_score(rubric, none_awarded) == 0


# =============================================================================
# Rules-based tests — the pass/fail outcome at the Config threshold
# (Req 10.2, 10.3)
# =============================================================================


def test_all_max_submission_passes() -> None:
    # A full-marks submission is above any (0, 1] pass fraction, so it passes.
    rubric = _rubric([10, 20, 30])
    assert is_passing(rubric, _scores(rubric, [10, 20, 30])) is True


def test_zero_submission_fails() -> None:
    # A zero-marks submission is below the pass fraction of a non-zero rubric.
    rubric = _rubric([10, 20, 30])
    assert is_passing(rubric, _scores(rubric, [0, 0, 0])) is False


def test_score_exactly_at_the_threshold_passes() -> None:
    # Req 10.2: the comparison is >=, so a weighted score landing exactly on the
    # Config threshold fraction of max_points passes. A total max of 100 makes
    # the threshold a whole number of points (0.7 * 100 = 70) reachable exactly.
    rubric = _rubric([100])
    threshold = _threshold_points(rubric)
    assert threshold == int(threshold)  # exact integer boundary for this rubric
    at_threshold = _scores(rubric, [int(threshold)])
    assert weighted_score(rubric, at_threshold) == int(threshold)
    assert is_passing(rubric, at_threshold) is True


def test_score_one_point_below_the_threshold_fails() -> None:
    # Req 10.3: one point below the exact threshold fails (strict < threshold).
    rubric = _rubric([100])
    threshold = int(_threshold_points(rubric))
    just_below = _scores(rubric, [threshold - 1])
    assert weighted_score(rubric, just_below) == threshold - 1
    assert is_passing(rubric, just_below) is False


# =============================================================================
# Rules-based tests — no outcome while the reviewer is pending (Req 10.4, 10.5)
# =============================================================================


def test_pending_reviewer_records_no_outcome() -> None:
    # Req 10.5: while the ProjectReviewer evaluation is pending, no review_outcome
    # is recorded — evaluate returns None rather than a pass/fail result.
    rubric = _rubric([10, 20, 30])
    submission = ProjectSubmissionRef(stable_id=_SUBMISSION, project_id=_PROJECT)
    reviewer = Fake_ProjectReviewer(pending=True)
    assert reviewer.review(submission, rubric) is None
    assert evaluate(submission, rubric, reviewer) is None


def test_completed_passing_review_records_a_passing_outcome() -> None:
    # Req 10.2, 10.4: once the reviewer completes with full marks, evaluate
    # returns a concrete (non-None) outcome and it is the passing one.
    rubric = _rubric([10, 20, 30])
    submission = ProjectSubmissionRef(stable_id=_SUBMISSION, project_id=_PROJECT)
    reviewer = Fake_ProjectReviewer(
        scores={c.stable_id: c.max_points for c in rubric.criteria},
    )
    outcome = evaluate(submission, rubric, reviewer)
    assert outcome is not None
    scores = reviewer.review(submission, rubric)
    assert scores is not None
    assert is_passing(rubric, scores) is True


def test_completed_failing_review_records_a_not_passing_outcome() -> None:
    # Req 10.3, 10.4: a completed review with zero marks yields a concrete
    # (non-None) outcome distinct from the passing one.
    rubric = _rubric([10, 20, 30])
    submission = ProjectSubmissionRef(stable_id=_SUBMISSION, project_id=_PROJECT)
    reviewer = Fake_ProjectReviewer(default_points=0)
    outcome = evaluate(submission, rubric, reviewer)
    assert outcome is not None
    scores = reviewer.review(submission, rubric)
    assert scores is not None
    assert is_passing(rubric, scores) is False
    # The pass and fail outcomes are distinguishable.
    passing_reviewer = Fake_ProjectReviewer(
        scores={c.stable_id: c.max_points for c in rubric.criteria},
    )
    assert outcome != evaluate(submission, rubric, passing_reviewer)


def test_toggling_a_pending_reviewer_complete_reveals_the_outcome() -> None:
    # Req 10.5: a submission stays outcome-less (None) while pending and gains a
    # concrete outcome only once the reviewer completes — never before.
    rubric = _rubric([10, 20, 30])
    submission = ProjectSubmissionRef(stable_id=_SUBMISSION, project_id=_PROJECT)
    reviewer = Fake_ProjectReviewer(
        scores={c.stable_id: c.max_points for c in rubric.criteria},
        pending=True,
    )
    assert evaluate(submission, rubric, reviewer) is None
    reviewer.complete()
    assert evaluate(submission, rubric, reviewer) is not None


# =============================================================================
# Property 18: Rubric outcome follows the weighted score against the threshold
# (Req 10.1, 10.2, 10.3, 10.5)
# =============================================================================


@settings(max_examples=200)
@given(
    max_points=_rubric_shapes,
    awarded_seed=st.data(),
    pending=st.booleans(),
)
def test_property_18_rubric_outcome_follows_weighted_score_against_threshold(
    max_points: list[int],
    awarded_seed: st.DataObject,
    pending: bool,
) -> None:
    """Feature: learning-core, Property 18: Rubric outcome follows the weighted score against the threshold.

    For any submission and rubric, the weighted score is the sum of its criteria
    bounded by each criterion's max_points, and the recorded outcome is passing
    if and only if that score meets or exceeds the Config pass threshold — and no
    outcome is recorded while the ProjectReviewer evaluation is pending
    (Req 10.1, 10.2, 10.3, 10.5).
    """
    rubric = _rubric(max_points)
    submission = ProjectSubmissionRef(stable_id=_SUBMISSION, project_id=_PROJECT)

    # Awarded points deliberately overshoot and undershoot each max so the
    # per-criterion clamp (Req 10.2) is exercised alongside in-range awards.
    awarded = [
        awarded_seed.draw(st.integers(min_value=-10, max_value=mp + 10), label=f"award_{i}")
        for i, mp in enumerate(max_points)
    ]
    scores = _scores(rubric, awarded)

    # The weighted score is the sum of per-criterion awards, each clamped to
    # 0..max_points, and is itself bounded by the rubric's total max_points.
    expected_clamped = sum(min(max(a, 0), mp) for a, mp in zip(awarded, max_points, strict=True))
    score = weighted_score(rubric, scores)
    assert score == expected_clamped
    assert 0 <= score <= total_max_points(rubric)

    # The outcome is passing iff the weighted score meets or exceeds the Config
    # threshold fraction of the total max_points (the >= boundary, Req 10.2/10.3).
    threshold = RUBRIC_PASS_THRESHOLD * total_max_points(rubric)
    assert is_passing(rubric, scores) is (score >= threshold)

    # The reviewer routes the same awards through the extension point. While
    # pending, no outcome is recorded (None); once complete, the outcome exists
    # and agrees with is_passing (Req 10.4, 10.5).
    reviewer = Fake_ProjectReviewer(
        scores={
            criterion.stable_id: award
            for criterion, award in zip(rubric.criteria, awarded, strict=True)
        },
        pending=pending,
    )
    outcome = evaluate(submission, rubric, reviewer)
    if pending:
        assert outcome is None
    else:
        assert outcome is not None


# =============================================================================
# Property 18 (threshold boundary) — exactly-at passes, one point below fails
# =============================================================================


@settings(max_examples=200)
@given(total_max=st.integers(min_value=1, max_value=1000))
def test_property_18_threshold_boundary_is_inclusive_from_below(total_max: int) -> None:
    """Feature: learning-core, Property 18: Rubric outcome follows the weighted score against the threshold.

    For any rubric total, a weighted score at the exact Config threshold (the
    ceiling of the pass fraction of max_points) passes under the >= comparison,
    and the integer one point below it fails (Req 10.2, 10.3).
    """
    rubric = _rubric([total_max])  # single criterion carries the whole scale
    threshold = RUBRIC_PASS_THRESHOLD * total_max

    # The smallest integer award that meets or exceeds the threshold passes.
    at_or_above = min(math.ceil(threshold), total_max)
    assert is_passing(rubric, _scores(rubric, [at_or_above])) is True

    # One integer point below that boundary is strictly under the threshold and
    # must fail — unless the boundary is already 0 (an empty failing region).
    if at_or_above - 1 >= 0 and (at_or_above - 1) < threshold:
        assert is_passing(rubric, _scores(rubric, [at_or_above - 1])) is False
