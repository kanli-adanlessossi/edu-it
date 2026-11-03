"""Tightened reviewer rule and the ``include_review_rule`` switch (ai-content Req 6.1-6.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.cli import main
from edu_it_content.validation import _RULE_REVIEWER, load_content, validate_content


def _write_root(tmp_path: Path, provenance_extra: dict[str, Any], origin: str = "ai") -> Path:
    root = tmp_path / "content"
    root.mkdir(parents=True)
    item = {
        "provenance": {
            "stable_id": "tr_1",
            "content_version": "cv_1",
            "origin": origin,
            **provenance_extra,
        },
        "title": {"fr": "Titre", "en": "Title"},
        "status": "published",
    }
    (root / "items.json").write_text(json.dumps({"track": [item]}), encoding="utf-8")
    return root


_BY = {"reviewed_by": "usr_r"}
_AT = {"reviewed_at": "2026-01-01T00:00:00+00:00"}


@pytest.mark.parametrize("extra", [{}, _BY, _AT])
def test_ai_item_missing_either_reviewer_field_fails_naming_item(
    tmp_path: Path, extra: dict[str, Any]
) -> None:
    content = load_content(_write_root(tmp_path, extra))

    failures = validate_content(content)

    assert [(f.rule, f.stable_id) for f in failures] == [(_RULE_REVIEWER, "tr_1")]


def test_ai_item_with_both_fields_and_human_item_pass(tmp_path: Path) -> None:
    assert validate_content(load_content(_write_root(tmp_path, {**_BY, **_AT}))) == []
    assert validate_content(load_content(_write_root(tmp_path / "h", {}, origin="human"))) == []


def test_include_review_rule_false_skips_only_reviewer_rule(tmp_path: Path) -> None:
    content = load_content(_write_root(tmp_path, {}))

    assert validate_content(content, include_review_rule=False) == []


def test_cli_always_applies_review_rule(tmp_path: Path) -> None:
    root = _write_root(tmp_path, {})

    assert main(["validate", "--content-root", str(root)]) == 1
