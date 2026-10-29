"""Example tests for deferred archiving during active use (task 11.3, Req 26.2, 26.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import load_content


def _prov(stable_id: str) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": "human",
        "reviewed_by": "usr_reviewer",
    }


def _title() -> dict[str, str]:
    return {"fr": "Titre", "en": "Title"}


def _valid_catalog() -> dict[str, list[dict[str, Any]]]:
    return {
        "track": [{"provenance": _prov("tr_1"), "title": _title(), "status": "published"}],
        "module": [
            {
                "provenance": _prov("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _title(),
            }
        ],
        "lesson": [
            {
                "provenance": _prov("ls_1"),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _title(),
            }
        ],
    }


def _loaded(items: dict[str, list[dict[str, Any]]], tmp_path: Path) -> Any:
    root = tmp_path / "content"
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")
    return load_content(root)


def test_removed_item_with_active_session_is_deferred_then_archived(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    store.start_session("s1", "ls_1")

    catalog["lesson"] = []
    report = sync_content(store, _loaded(catalog, tmp_path))

    assert report.deferred == ("ls_1",)
    assert "ls_1" not in report.archived
    assert store.get("ls_1").status is ContentStatus.ACTIVE  # type: ignore[union-attr]

    # Re-running while the session is active stays deferred and never deletes.
    assert sync_content(store, _loaded(catalog, tmp_path)).deferred == ("ls_1",)

    store.complete_session("s1")
    final = sync_content(store, _loaded(catalog, tmp_path))

    assert final.archived == ("ls_1",)
    assert final.deferred == ()
    assert store.get("ls_1").status is ContentStatus.ARCHIVED  # type: ignore[union-attr]


def test_removed_item_without_sessions_archives_immediately(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    store.start_session("s1", "ls_1")
    store.complete_session("s1")

    catalog["lesson"] = []
    report = sync_content(store, _loaded(catalog, tmp_path))

    assert report.archived == ("ls_1",)
    assert report.deferred == ()


def test_in_flight_session_stays_pinned_to_its_content_version(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    original_payload = store.get("ls_1").payload  # type: ignore[union-attr]
    pinned = store.start_session("s1", "ls_1")

    catalog["lesson"][0]["title"] = {"fr": "Edité", "en": "Edited"}
    catalog["lesson"][0]["provenance"]["content_version"] = "cv_2"
    sync_content(store, _loaded(catalog, tmp_path))

    assert store.get("ls_1").content_version == "cv_2"  # type: ignore[union-attr]
    assert store.session_pinned_version("s1") == pinned == "cv_1"
    assert store.payload_at_version("ls_1", pinned) == original_payload
