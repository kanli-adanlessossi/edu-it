"""Strict and ``--allow-pending`` validation modes for labs (task 14.8).

Covers Requirements 20.9, 20.13, 20.14, 23.6, 23.7, 23.9.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.cli import main
from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import (
    LoadedContent,
    apply_pending_allowance,
    load_content,
    load_pending_review_list,
    validate_content,
)

REVIEWER_RULE = "ai-reviewer-present"


def _loc(text: str = "x") -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str, origin: str = "human", reviewer: str | None = "usr_r") -> dict[str, Any]:
    prov: dict[str, Any] = {"stable_id": stable_id, "content_version": "cv_1", "origin": origin}
    if reviewer is not None:
        prov["reviewed_by"] = reviewer
        prov["reviewed_at"] = "2026-01-01T00:00:00+00:00"
        prov["reviewed_at"] = "2026-01-01T00:00:00+00:00"
    return prov


def _outcome(code: str, key: str) -> dict[str, Any]:
    return {
        "code": code,
        "exit_code": 0 if code == "pass" else 1,
        "feedback_key": key,
        "message": _loc(key),
    }


def _items(*, origin: str = "ai", reviewer: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """A valid catalog with one lab (``lb_1``); by default AI-authored, unreviewed."""
    return {
        "track": [{"provenance": _prov("tr_1"), "title": _loc(), "status": "published"}],
        "module": [
            {"provenance": _prov("md_1"), "track_stable_id": "tr_1", "ordinal": 0, "title": _loc()}
        ],
        "lesson": [
            {"provenance": _prov("ls_1"), "module_stable_id": "md_1", "ordinal": 0, "title": _loc()}
        ],
        "concept": [{"provenance": _prov("cn_1"), "title": _loc()}],
        "lab": [
            {
                "provenance": _prov("lb_1", origin=origin, reviewer=reviewer),
                "lesson_stable_id": "ls_1",
                "title": _loc(),
                "kind": "terminal",
                "image": "linux-base",
                "concept_stable_ids": ["cn_1"],
                "instructions": _loc(),
                "setup_script": "labs/setup.sh",
                "check_bundle": "labs/bundle",
            }
        ],
        "lab_task": [
            {
                "stable_id": "lt_1",
                "lab_stable_id": "lb_1",
                "ordinal": 0,
                "title": _loc(),
                "description": _loc(),
            }
        ],
        "lab_check": [
            {
                "stable_id": "lc_1",
                "lab_task_stable_id": "lt_1",
                "timeout_seconds": 4,
                "script": "check.sh",
                "outcomes": [_outcome("pass", "a.pass"), _outcome("fail", "a.fail")],
            }
        ],
    }


def _write_root(repo: Path, items: dict[str, list[dict[str, Any]]], listed: list[str]) -> Path:
    """Write ``items`` as per-type files under ``<repo>/content/seed`` with a pending list."""
    (repo / "lab-images" / "linux-base").mkdir(parents=True, exist_ok=True)
    (repo / "labs" / "bundle").mkdir(parents=True, exist_ok=True)
    (repo / "labs" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    root = repo / "content"
    track = root / "seed"
    track.mkdir(parents=True, exist_ok=True)
    for type_key, entries in items.items():
        (track / f"{type_key}.json").write_text(json.dumps(entries), encoding="utf-8")
    (track / "pending-review.json").write_text(json.dumps({"stable_ids": listed}), encoding="utf-8")
    return root


def _rules(root: Path, *, allow_pending: bool) -> list[tuple[str, str]]:
    content = load_content(root)
    failures = validate_content(content)
    if allow_pending:
        failures = apply_pending_allowance(failures, content, load_pending_review_list(root))
    return sorted((f.rule, f.stable_id) for f in failures)


def test_listed_lab_missing_only_reviewer_passes_pending_and_reports_exact_rule_strictly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _write_root(tmp_path, _items(), ["lb_1"])

    assert _rules(root, allow_pending=False) == [(REVIEWER_RULE, "lb_1")]
    assert _rules(root, allow_pending=True) == []

    assert main(["validate", "--content-root", str(root), "--allow-pending"]) == 0
    capsys.readouterr()
    assert main(["validate", "--content-root", str(root)]) != 0
    assert REVIEWER_RULE in capsys.readouterr().err


def test_listed_lab_with_another_failure_fails_pending(tmp_path: Path) -> None:
    items = _items()
    items["lab"][0]["check_bundle"] = "labs/missing-bundle"  # a non-reviewer defect
    root = _write_root(tmp_path, items, ["lb_1"])

    failing = _rules(root, allow_pending=True)
    assert failing, "a listed lab with another failure must still fail"
    assert all(rule != REVIEWER_RULE for rule, _ in failing)
    assert main(["validate", "--content-root", str(root), "--allow-pending"]) != 0


def test_unlisted_unreviewed_lab_fails_pending(tmp_path: Path) -> None:
    root = _write_root(tmp_path, _items(), [])

    assert _rules(root, allow_pending=True) == [(REVIEWER_RULE, "lb_1")]
    assert main(["validate", "--content-root", str(root), "--allow-pending"]) != 0


def test_listing_a_different_id_does_not_tolerate_the_lab(tmp_path: Path) -> None:
    root = _write_root(tmp_path, _items(), ["lb_other"])

    assert _rules(root, allow_pending=True) == [(REVIEWER_RULE, "lb_1")]


@pytest.mark.parametrize("listed", [["lb_1"], []])
def test_sync_refuses_unreviewed_ai_lab_listed_or_not(tmp_path: Path, listed: list[str]) -> None:
    root = _write_root(tmp_path, _items(), listed)
    store = In_Memory_ContentStore()

    with pytest.raises(ValueError, match=REVIEWER_RULE):
        sync_content(store, load_content(root))
    assert store.list_active_ids() == frozenset()


def test_reviewed_lab_validates_strictly_once_list_is_emptied(tmp_path: Path) -> None:
    root = _write_root(tmp_path, _items(reviewer="usr_r"), [])

    assert _rules(root, allow_pending=False) == []
    assert _rules(root, allow_pending=True) == []
    assert main(["validate", "--content-root", str(root)]) == 0

    # Reviewed and emptied list: sync now succeeds.
    store = In_Memory_ContentStore()
    sync_content(store, load_content(root))
    assert "lb_1" in store.list_active_ids()


def test_reviewed_lab_is_validated_strictly_even_if_still_listed(tmp_path: Path) -> None:
    """Once reviewed, any other failure is reported regardless of list membership."""
    items = _items(reviewer="usr_r")
    items["lab"][0]["check_bundle"] = "labs/missing-bundle"
    root = _write_root(tmp_path, items, [])

    assert _rules(root, allow_pending=True)
    assert main(["validate", "--content-root", str(root), "--allow-pending"]) != 0


def test_input_items_are_not_mutated_by_the_allowance(tmp_path: Path) -> None:
    items = _items()
    before = copy.deepcopy(items)
    content = LoadedContent(raw_items=items, root=tmp_path / "content")
    apply_pending_allowance(validate_content(content), content, frozenset({"lb_1"}))
    assert items == before
