"""Tests for the Generation_Report, PR_Template and CODEOWNERS writers.

Validates: Requirements 5.6, 5.7, 9.1, 9.2, 9.3, 9.4
"""

from __future__ import annotations

from pathlib import Path

import pytest
from edu_it_domain.ai.budget import BudgetKind
from edu_it_domain.ai.verification import FindingKind, VerificationFinding
from edu_it_worker.ai.report import (
    CODEOWNERS_PATH,
    BudgetRemaining,
    DiscardedUnit,
    GeneratedItem,
    GenerationReport,
    NotGenerated,
    ReportedFinding,
    ensure_codeowners_line,
    governance_files,
    pr_template_path,
    render_pr_template,
    render_report,
    report_path,
)

TRACK = "networking-basics"


def _item(sid: str, cost: int = 150) -> GeneratedItem:
    return GeneratedItem(
        stable_id=sid,
        kind="lesson",
        model="m-1",
        prompt_id="lesson",
        prompt_version="2",
        content_version="sha256:abc",
        input_tokens=100,
        output_tokens=50,
        cost_cents=cost,
    )


def _report(**kw: object) -> GenerationReport:
    base: dict[str, object] = {
        "track": TRACK,
        "stage": "lessons",
        "run": "r1",
        "branch": "generate/networking-basics/lessons/r1",
        "live": True,
        "items": [_item("lesson.b"), _item("lesson.a", 250)],
        "findings": [
            ReportedFinding(
                "lesson.a", VerificationFinding(FindingKind.DUPLICATE, "t1", "same\ntext")
            ),
            ReportedFinding("lesson.b", VerificationFinding(FindingKind.DISAGREEMENT, "t2")),
        ],
        "remaining": BudgetRemaining(daily_cents=900, track_cents=400, cap_cents=1200),
        "discarded": [DiscardedUnit("lesson.c", "cap reached")],
        "not_generated": [NotGenerated("lesson.d", "budget stop")],
        "extra_cost_cents": 50,
        "extra_input_tokens": 10,
        "extra_output_tokens": 5,
        "extra_models": ["m-2"],
    }
    base.update(kw)
    return GenerationReport(**base)  # type: ignore[arg-type]


def test_report_lists_everything() -> None:
    text = render_report(_report())
    assert text.index("lesson.a") < text.index("lesson.b")
    assert "Findings recorded: 2" in text
    assert "1. [duplicate] item `lesson.a` target `t1`: same text" in text
    assert "2. [disagreement] item `lesson.b` target `t2`" in text
    assert "`lesson.c`: cap reached" in text
    assert "`lesson.d`: budget stop" in text
    assert "Total cost: USD 4.50" in text
    assert "Input tokens: 210" in text
    assert "Output tokens: 105" in text
    assert "Models: m-1, m-2" in text
    assert "Prompt: lesson v2" in text
    assert "Mode: live" in text


def test_smallest_remaining_budget() -> None:
    assert BudgetRemaining(900, 400, 1200).smallest() == (BudgetKind.TRACK, 400)
    assert BudgetRemaining(900, 400, 100).smallest() == (BudgetKind.JOB, 100)
    assert BudgetRemaining(900, 1000).smallest() == (BudgetKind.DAILY, 900)
    assert BudgetRemaining(-5, 10).smallest() == (BudgetKind.DAILY, 0)
    text = render_report(_report())
    assert "Smallest remaining: USD 4.00 (track)" in text
    assert "Cost cap: USD 12.00" in text


def test_empty_report_and_deterministic() -> None:
    empty = _report(items=[], findings=[], discarded=[], not_generated=[], extra_models=[])
    text = render_report(empty)
    assert "Findings recorded: 0" in text
    assert "Models: none" in text
    assert render_report(empty) == text


def test_paths_validated() -> None:
    assert report_path(TRACK, "lessons", "r1") == f"docs/generation/{TRACK}/lessons-r1.md"
    assert pr_template_path("labs") == ".github/PULL_REQUEST_TEMPLATE/ai-generation-labs.md"
    for bad in ("../x", "a/b", ""):
        with pytest.raises(ValueError):
            report_path(TRACK, "lessons", bad)
    with pytest.raises(ValueError):
        report_path("../x", "lessons", "r1")
    with pytest.raises(ValueError):
        pr_template_path("nope")


def test_pr_template_checklist() -> None:
    text = render_pr_template("quizzes")
    for word in ("Correctness", "FR/EN parity", "Glossary use", "Sources", "Verification findings"):
        assert f"- [ ] **{word}**" in text
    assert "quizzes" in text


def test_codeowners_appends_missing_track() -> None:
    out = ensure_codeowners_line("# c\n/content/other/ @o/team", TRACK)
    assert out.endswith(f"/content/{TRACK}/ @REPLACE-ME-ORG/{TRACK}-reviewers\n")
    assert "/content/other/ @o/team\n" in out
    assert (
        ensure_codeowners_line(None, TRACK)
        == f"/content/{TRACK}/ @REPLACE-ME-ORG/{TRACK}-reviewers\n"
    )


def test_codeowners_keeps_existing_line_and_is_idempotent() -> None:
    text = f"/content/{TRACK}/  @real/team\n"
    assert ensure_codeowners_line(text, TRACK) == text
    once = ensure_codeowners_line("# only comment\n", TRACK)
    assert ensure_codeowners_line(once, TRACK) == once
    assert ensure_codeowners_line(f"# /content/{TRACK}/\n", TRACK).count(f"/content/{TRACK}/") == 2


def test_governance_files_and_repo_codeowners_unchanged() -> None:
    repo = Path(__file__).resolve().parents[3]
    current = (repo / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    files = governance_files(_report(), {CODEOWNERS_PATH: current})
    assert set(files) == {
        f"docs/generation/{TRACK}/lessons-r1.md",
        ".github/PULL_REQUEST_TEMPLATE/ai-generation-lessons.md",
        CODEOWNERS_PATH,
    }
    assert files[CODEOWNERS_PATH].decode() == current
