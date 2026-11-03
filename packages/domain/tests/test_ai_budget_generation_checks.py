"""Unit tests for ai/budget.py, ai/generation.py and ai/checks.py."""

from __future__ import annotations

import pytest
from edu_it_domain.ai.budget import BudgetKind, check_attempt
from edu_it_domain.ai.checks import (
    coverage,
    is_duplicate,
    normalise,
    readability_en,
    readability_fr,
    similarity,
)
from edu_it_domain.ai.generation import (
    STAGES,
    commit_set,
    missing_stage,
    plan_units,
    remaining_units,
    stage_allowed,
)


def test_budget_allows_below_limits() -> None:
    d = check_attempt(daily_spent_cents=0, track_spent_cents=0)
    assert d.allowed and d.reached is None


def test_budget_reports_each_kind() -> None:
    assert (
        check_attempt(daily_spent_cents=10, track_spent_cents=0, daily_limit_cents=10).reached
        is BudgetKind.DAILY
    )
    assert (
        check_attempt(daily_spent_cents=0, track_spent_cents=5, track_limit_cents=5).reached
        is BudgetKind.TRACK
    )
    assert (
        check_attempt(
            daily_spent_cents=0, track_spent_cents=0, job_spent_cents=7, cost_cap_cents=7
        ).reached
        is BudgetKind.JOB
    )


def test_budget_job_ignored_without_cap() -> None:
    assert check_attempt(daily_spent_cents=0, track_spent_cents=0, job_spent_cents=10**6).allowed


def test_stage_order_and_gate() -> None:
    assert STAGES == ("brief", "outline", "lessons", "quizzes", "labs", "project")
    assert stage_allowed("brief", [])
    assert not stage_allowed("lessons", ["brief"])
    assert missing_stage("lessons", ["brief"]) == "outline"
    assert stage_allowed("lessons", ["brief", "outline"])


def test_base_ref_widens_but_never_skips() -> None:
    assert stage_allowed("lessons", ["brief"], ["outline"])
    assert not stage_allowed("quizzes", ["brief", "outline"], ["outline"])
    with pytest.raises(ValueError):
        stage_allowed("nope", [])


def test_units_resume_and_commit() -> None:
    assert plan_units(["a", "b", "a"]) == ("a", "b")
    assert remaining_units(["a", "b", "c"], {"b"}) == ("a", "c")
    produced = {"a": ["fr", "en"], "b": ["fr"]}
    assert commit_set(["a", "b", "c"], produced) == ("a",)


def test_normalise_and_exact_duplicate() -> None:
    assert normalise("  Hello\n  WORLD ") == "hello world"
    assert is_duplicate("Hello   world", ["x", "HELLO world"])
    assert not is_duplicate("hello worlds", ["hello world"])


def test_coverage_names_uncovered() -> None:
    c = coverage(["a", "b", "c", "a"], {"a"})
    assert c.uncovered == ("b", "c")
    assert c.share == pytest.approx(1 / 3)
    assert coverage([], set()).share == 1.0


def test_readability() -> None:
    simple = "The cat sat. The dog ran."
    hard = "Internationalisation necessitates comprehensive infrastructural reconfiguration."
    assert readability_en(simple) > readability_en(hard)
    assert readability_fr("Le chat dort. Il fait beau.") > readability_fr(
        "L'internationalisation nécessite une reconfiguration infrastructurelle approfondie."
    )
    assert readability_en("") == 0.0


def test_similarity_containment() -> None:
    source = "the quick brown fox jumps over the lazy dog"
    assert similarity(source, [source]) == 1.0
    assert similarity("totally different words appear in this sentence", [source]) == 0.0
    assert similarity("too short", [source]) == 0.0
    half = "the quick brown fox jumps over a sleepy cat"
    assert 0 < similarity(half, [source]) < 1
