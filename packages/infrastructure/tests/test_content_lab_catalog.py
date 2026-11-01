"""Tests for ``Content_LabCatalog`` over a synced content store (task 15.1, Req 19.3, 20.10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import LoadedContent
from edu_it_domain.ports.lab_catalog import LabCatalog
from edu_it_infrastructure.adapters.lab_catalog import Content_LabCatalog


def _loc(text: str) -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": "human",
        "reviewed_by": "usr_r",
    }


def _outcome(code: str, key: str) -> dict[str, Any]:
    return {
        "code": code,
        "exit_code": 0 if code == "pass" else 1,
        "feedback_key": key,
        "message": _loc(key),
    }


def _items() -> dict[str, list[dict[str, Any]]]:
    def task(sid: str, ordinal: int, required: bool, hints: int) -> dict[str, Any]:
        return {
            "stable_id": sid,
            "lab_stable_id": "lb_1",
            "ordinal": ordinal,
            "title": _loc(sid),
            "description": _loc(f"desc-{sid}"),
            "required": required,
            "hints": [_loc(f"h{i}") for i in range(hints)],
        }

    return {
        "track": [{"provenance": _prov("tr_1"), "title": _loc("t"), "status": "published"}],
        "module": [
            {
                "provenance": _prov("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _loc("m"),
            }
        ],
        "lesson": [
            {
                "provenance": _prov("ls_1"),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _loc("l"),
            }
        ],
        "concept": [{"provenance": _prov("cn_1"), "title": _loc("c")}],
        "lab": [
            {
                "provenance": _prov("lb_1"),
                "lesson_stable_id": "ls_1",
                "title": _loc("Lab"),
                "kind": "terminal",
                "image": "linux-base",
                "concept_stable_ids": ["cn_1"],
                "instructions": _loc("i"),
                "setup_script": "labs/setup.sh",
                "check_bundle": "labs/bundle",
            }
        ],
        # Authored out of order to prove tasks are ordered by ordinal.
        "lab_task": [task("lt_2", 1, False, 0), task("lt_1", 0, True, 2)],
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


def _synced(repo: Path, items: dict[str, list[dict[str, Any]]]) -> In_Memory_ContentStore:
    store = In_Memory_ContentStore()
    sync_content(store, LoadedContent(raw_items=items, root=repo / "content"))
    return store


def test_satisfies_port_and_exposes_full_definition(repo: Path) -> None:
    catalog = Content_LabCatalog(_synced(repo, _items()))
    assert isinstance(catalog, LabCatalog)

    lab = catalog.get("lb_1")
    assert lab is not None
    assert (lab.kind, lab.image, lab.concept_ids) == ("terminal", "linux-base", ("cn_1",))
    assert lab.bundle_path == "labs/bundle"
    assert lab.text == {"fr": "Lab-fr", "en": "Lab-en"}
    assert [(t.task_id, t.required, t.hint_count) for t in lab.tasks] == [
        ("lt_1", True, 2),
        ("lt_2", False, 0),
    ]
    assert lab.tasks[0].text == {"fr": "desc-lt_1-fr", "en": "desc-lt_1-en"}
    (check,) = lab.checks
    assert (check.check_id, check.task_id, check.timeout_s) == ("lc_1", "lt_1", 4.0)
    assert check.outcomes == {"pass": "a.pass", "fail": "a.fail"}
    assert check.bundle_path == "check.sh"
    assert catalog.get("missing") is None


def test_labs_covering_and_archived_labs_are_excluded(repo: Path) -> None:
    store = _synced(repo, _items())
    catalog = Content_LabCatalog(store)
    assert [lab.lab_id for lab in catalog.labs_covering("cn_1")] == ["lb_1"]
    assert catalog.labs_covering("cn_other") == ()

    items = _items()
    for key in ("lab", "lab_task", "lab_check"):
        items[key] = []
    sync_content(store, LoadedContent(raw_items=items, root=repo / "content"))

    assert catalog.list_labs() == ()
    assert catalog.labs_covering("cn_1") == ()
    archived = catalog.get("lb_1")
    assert archived is not None and archived.archived
