"""Strict validation of the repository's existing content (ai-content task 2.4).

**Validates: Requirements 6.3, 7.5**
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from edu_it_content.cli import main
from edu_it_content.schemas import LessonDraft, LessonSectionDraft
from edu_it_content.validation import load_content, validate_content

REPO = Path(__file__).resolve().parents[3]
CONTENT_ROOT = REPO / "content"


def test_real_content_passes_strict_validator() -> None:
    """Validates: Requirements 6.3. Reviewer rule on, no pending allowance."""
    content = load_content(CONTENT_ROOT)
    assert content.raw_items, "expected the repository to ship content"
    failures = validate_content(content, repo_root=REPO, include_review_rule=True)
    assert failures == [], [f"{f.rule}:{f.stable_id}" for f in failures]


def test_real_content_passes_strict_cli(capsys: Any) -> None:
    """Validates: Requirements 6.3. CLI without --allow-pending exits zero."""
    assert main(["validate", "--content-root", str(CONTENT_ROOT)]) == 0
    assert "content valid" in capsys.readouterr().out


def test_lesson_draft_has_no_output_field() -> None:
    """Validates: Requirements 7.5. Commands only; outputs come from the sandbox."""
    for model in (LessonDraft, LessonSectionDraft):
        assert not any("output" in name for name in model.model_fields)
