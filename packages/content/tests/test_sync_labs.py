"""Lab behaviour of ``edu-it content sync`` (task 14.4, Req 20.10, 20.11, 23.7)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import LoadedContent


def _loc(text: str = "x") -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str, origin: str = "human", reviewer: str | None = "usr_r") -> dict[str, Any]:
    prov: dict[str, Any] = {"stable_id": stable_id, "content_version": "cv_1", "origin": origin}
    if reviewer is not None:
        prov["reviewed_by"] = reviewer
    return prov


def _outcome(code: str, key: str) -> dict[str, Any]:
    return {
        "code": code,
        "exit_code": 0 if code == "pass" else 1,
        "feedback_key": key,
        "message": _loc(key),
    }


def _items() -> dict[str, list[dict[str, Any]]]:
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
                "provenance": _prov("lb_1"),
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "lab-images" / "linux-base").mkdir(parents=True)
    (tmp_path / "labs" / "bundle").mkdir(parents=True)
    (tmp_path / "labs" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return tmp_path


def _content(repo: Path, items: dict[str, list[dict[str, Any]]]) -> LoadedContent:
    return LoadedContent(raw_items=items, root=repo / "content")


def test_second_sync_is_a_no_op_and_upserts_labs_tasks_checks(repo: Path) -> None:
    store = In_Memory_ContentStore()
    first = sync_content(store, _content(repo, _items()))
    assert {"lb_1", "lt_1", "lc_1"} <= set(first.inserted)

    second = sync_content(store, _content(repo, _items()))
    assert not second.changed
    assert second.orphaned == ()


def test_edit_keeps_completions_and_orphans_changed_items(repo: Path) -> None:
    store = In_Memory_ContentStore()
    sync_content(store, _content(repo, _items()))
    store.seed_completion("lt_1", "c1")

    edited = copy.deepcopy(_items())
    edited["lab_task"][0]["description"] = _loc("changed")
    report = sync_content(store, _content(repo, edited))

    assert report.updated == ("lt_1",)
    assert report.orphaned == ("lt_1",)
    assert store.orphaned_ids() == frozenset({"lt_1"})
    assert store.completion_keys() == frozenset({("lt_1", "c1")})


def test_removed_lab_is_archived_never_deleted_and_progress_orphaned(repo: Path) -> None:
    store = In_Memory_ContentStore()
    sync_content(store, _content(repo, _items()))
    store.seed_completion("lt_1", "c1")

    items = _items()
    for key in ("lab", "lab_task", "lab_check"):
        items[key] = []
    report = sync_content(store, _content(repo, items))

    assert set(report.archived) == {"lb_1", "lt_1", "lc_1"}
    assert set(report.orphaned) == {"lb_1", "lt_1", "lc_1"}
    stored = store.get("lb_1")
    assert stored is not None and stored.status is ContentStatus.ARCHIVED
    assert store.completion_keys() == frozenset({("lt_1", "c1")})


def test_unreviewed_ai_lab_is_refused_and_nothing_is_written(repo: Path) -> None:
    store = In_Memory_ContentStore()
    items = _items()
    items["lab"][0]["provenance"] = _prov("lb_1", origin="ai", reviewer=None)

    with pytest.raises(ValueError, match="ai-reviewer-present"):
        sync_content(store, _content(repo, items))
    assert store.list_active_ids() == frozenset()
