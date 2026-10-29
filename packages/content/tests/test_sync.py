"""Example tests for the idempotent ``content sync`` upsert (task 11.1, Req 14, 26.1).

These are the worked-example confidence checks that the sync keyed by
``stable_id`` behaves as the design's "``edu-it content sync``" section and D3
§10 specify. They exercise the framework-free core
(:func:`edu_it_content.sync.sync_content`) against the in-memory
:class:`~edu_it_content.content_store.In_Memory_ContentStore`, plus one
end-to-end check through the ``edu-it content sync`` CLI against a fixture
content root:

- A first sync **inserts** every authored item (Req 14.1).
- Re-syncing **edited** content **updates in place** and bumps
  ``content_version`` without ever creating a duplicate ``stable_id`` (Req 14.1).
- A second sync over **unchanged** content is a **no-op** — nothing inserted,
  updated, or archived (Req 14.2). This is the precise behavior Property 21
  (task 11.2) will assert universally.
- **Completion records are preserved** across an edit and across an archive
  (Req 14.3, 26.1): a planted completion survives.
- A **removed** item is **archived, not hard-deleted** (Req 14.4): its row stays,
  with ``ARCHIVED`` status, and is re-activated (not duplicated) if re-authored.

The all-valid catalog mirrors the ``validate`` smoke fixture so the two commands
stay consistent; sync validates first, so an invalid catalog is rejected without
writing (that rejection path is asserted here too).

Property 21 itself (the universal idempotence + completion-preservation property)
is task 11.2 and is intentionally NOT written here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.cli import main
from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import load_content


def _localized(fr: str = "Titre", en: str = "Title") -> dict[str, str]:
    return {"fr": fr, "en": en}


def _provenance(
    stable_id: str,
    *,
    content_version: str = "cv_1",
    origin: str = "human",
    reviewed: bool = True,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "stable_id": stable_id,
        "content_version": content_version,
        "origin": origin,
    }
    if reviewed:
        provenance["reviewed_by"] = "usr_reviewer"
    return provenance


def _valid_catalog() -> dict[str, list[dict[str, Any]]]:
    """A tiny but complete, internally-consistent, all-valid catalog."""
    return {
        "track": [
            {"provenance": _provenance("tr_1"), "title": _localized(), "status": "published"}
        ],
        "module": [
            {
                "provenance": _provenance("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "lesson": [
            {
                "provenance": _provenance("ls_1"),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "concept": [
            {"provenance": _provenance("cp_1"), "title": _localized()},
            {"provenance": _provenance("cp_2"), "title": _localized()},
        ],
        "prerequisite": [
            {
                "stable_id": "pr_1",
                "concept_stable_id": "cp_2",
                "requires_concept_stable_id": "cp_1",
            }
        ],
    }


def _write_items(root: Path, items: dict[str, list[dict[str, Any]]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")


def _loaded(items: dict[str, list[dict[str, Any]]], tmp_path: Path) -> Any:
    """Write ``items`` to a content root under ``tmp_path`` and load it."""
    root = tmp_path / "content"
    _write_items(root, items)
    return load_content(root)


# -- Insert ------------------------------------------------------------------


def test_first_sync_inserts_every_item(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()

    report = sync_content(store, _loaded(catalog, tmp_path))

    expected_ids = {"tr_1", "md_1", "ls_1", "cp_1", "cp_2", "pr_1"}
    assert set(report.inserted) == expected_ids
    assert report.updated == ()
    assert report.archived == ()
    assert store.list_active_ids() == frozenset(expected_ids)
    # Every item is stored ACTIVE, addressed by its stable_id.
    for stable_id in expected_ids:
        stored = store.get(stable_id)
        assert stored is not None
        assert stored.status is ContentStatus.ACTIVE


# -- No-op on unchanged second run (Req 14.2) --------------------------------


def test_second_sync_over_unchanged_content_is_a_noop(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    loaded = _loaded(catalog, tmp_path)

    first = sync_content(store, loaded)
    assert first.changed is True

    # A fresh load of the identical content root — the second run.
    second = sync_content(store, load_content(tmp_path / "content"))

    assert second.inserted == ()
    assert second.updated == ()
    assert second.archived == ()
    assert set(second.unchanged) == set(first.inserted)
    assert second.changed is False
    # The store observed no content-changing write on the second run.
    assert len(store.changed_upserts()) == len(first.inserted)


# -- Update in place, bump version, never duplicate (Req 14.1) ---------------


def test_editing_an_item_updates_in_place_and_bumps_version(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    before = store.get("ls_1")
    assert before is not None

    # Edit the lesson title and bump its authored content_version.
    catalog["lesson"][0]["title"] = _localized(fr="Nouveau", en="New")
    catalog["lesson"][0]["provenance"]["content_version"] = "cv_2"

    report = sync_content(store, _loaded(catalog, tmp_path))

    assert report.updated == ("ls_1",)
    assert "ls_1" not in report.inserted
    after = store.get("ls_1")
    assert after is not None
    assert after.content_version == "cv_2"
    assert after.payload != before.payload
    # Exactly one row for the stable_id: no duplicate was created.
    assert "ls_1" in store.list_active_ids()


def test_reattaching_a_reviewer_is_not_a_content_change(tmp_path: Path) -> None:
    # An unreviewed-but-otherwise-valid human item, then reviewed on re-sync:
    # attaching the reviewer must not count as a content edit (D3 §1.2).
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    catalog["concept"][0]["provenance"] = _provenance("cp_1", reviewed=False)
    sync_content(store, _loaded(catalog, tmp_path))

    catalog["concept"][0]["provenance"]["reviewed_by"] = "usr_reviewer"
    report = sync_content(store, _loaded(catalog, tmp_path))

    assert "cp_1" in report.unchanged
    assert "cp_1" not in report.updated


# -- Completion preservation (Req 14.3, 26.1) --------------------------------


def test_completion_is_preserved_across_an_edit(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    store.seed_completion("ls_1", "lc_1")

    # Edit the lesson and re-sync.
    catalog["lesson"][0]["title"] = _localized(fr="Edité", en="Edited")
    catalog["lesson"][0]["provenance"]["content_version"] = "cv_2"
    sync_content(store, _loaded(catalog, tmp_path))

    assert ("ls_1", "lc_1") in store.completion_keys()


# -- Archive removed items, never hard-delete (Req 14.4) ---------------------


def test_removed_item_is_archived_not_deleted(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))

    # Remove the prerequisite edge from the authored set.
    catalog["prerequisite"] = []
    report = sync_content(store, _loaded(catalog, tmp_path))

    assert report.archived == ("pr_1",)
    archived = store.get("pr_1")
    assert archived is not None  # not hard-deleted
    assert archived.status is ContentStatus.ARCHIVED
    assert "pr_1" not in store.list_active_ids()


def test_archived_item_completion_is_preserved(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))
    store.seed_completion("ls_1", "lc_1")

    # Remove the lesson; it must archive but keep the completion row valid.
    catalog["lesson"] = []
    sync_content(store, _loaded(catalog, tmp_path))

    assert store.get("ls_1") is not None
    assert store.get("ls_1").status is ContentStatus.ARCHIVED  # type: ignore[union-attr]
    assert ("ls_1", "lc_1") in store.completion_keys()


def test_reauthoring_an_archived_item_reactivates_without_duplicating(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    sync_content(store, _loaded(catalog, tmp_path))

    full = _valid_catalog()
    catalog["prerequisite"] = []
    sync_content(store, _loaded(catalog, tmp_path))
    assert store.get("pr_1").status is ContentStatus.ARCHIVED  # type: ignore[union-attr]

    # Re-author the removed edge: it comes back ACTIVE, not as a duplicate.
    report = sync_content(store, _loaded(full, tmp_path))

    assert "pr_1" in report.updated or "pr_1" in report.inserted
    reactivated = store.get("pr_1")
    assert reactivated is not None
    assert reactivated.status is ContentStatus.ACTIVE
    assert "pr_1" in store.list_active_ids()


# -- Sync refuses invalid content (Req 14) -----------------------------------


def test_sync_refuses_invalid_content_without_writing(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()
    catalog = _valid_catalog()
    # Break a reference so validation fails.
    catalog["module"][0]["track_stable_id"] = "tr_missing"

    with pytest.raises(ValueError, match="cannot sync invalid content"):
        sync_content(store, _loaded(catalog, tmp_path))

    assert store.list_active_ids() == frozenset()


# -- CLI end-to-end against a fixture content root ---------------------------


def test_cli_sync_valid_content_exits_zero(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "content"
    _write_items(root, _valid_catalog())

    exit_code = main(["sync", "--content-root", str(root)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "content synced" in out
    assert "6 inserted" in out


def test_cli_sync_invalid_content_exits_one(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["module"][0]["track_stable_id"] = "tr_missing"
    _write_items(root, catalog)

    exit_code = main(["sync", "--content-root", str(root)])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_sync_missing_root_exits_load_error(tmp_path: Path, capsys: Any) -> None:
    missing = tmp_path / "nope"

    exit_code = main(["sync", "--content-root", str(missing)])

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err
