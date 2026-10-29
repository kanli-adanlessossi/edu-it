"""Tests for the per-type content layout loaded by ``load_content`` (task 13).

Seed content lives as ``content/<track>/<type>.json`` (one list per content
type). The loader merges these across files and track directories, still
supports a root ``items.json``, and reports unknown type files / malformed JSON
as :class:`ContentLoadError`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.cli import main
from edu_it_content.validation import ContentLoadError, load_content, validate_content


def _loc() -> dict[str, str]:
    return {"fr": "Titre", "en": "Title"}


def _prov(stable_id: str) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": "human",
        "reviewed_by": "usr_reviewer",
    }


def _track(stable_id: str) -> dict[str, Any]:
    return {"provenance": _prov(stable_id), "title": _loc(), "status": "published"}


def _module(stable_id: str, track: str) -> dict[str, Any]:
    return {
        "provenance": _prov(stable_id),
        "track_stable_id": track,
        "ordinal": 0,
        "title": _loc(),
    }


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_merges_per_type_files_across_multiple_track_dirs(tmp_path: Path) -> None:
    _write(tmp_path / "a" / "tracks.json", [_track("tr_a")])
    _write(tmp_path / "a" / "modules.json", [_module("md_a", "tr_a")])
    _write(tmp_path / "b" / "tracks.json", [_track("tr_b")])
    _write(tmp_path / "b" / "modules.json", [_module("md_b", "tr_b")])

    content = load_content(tmp_path)

    assert len(content.raw_items["track"]) == 2
    assert len(content.raw_items["module"]) == 2
    assert validate_content(content) == []


def test_cross_file_references_resolve_and_rubric_criteria_stem(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "tracks.json", [_track("tr_1")])
    _write(tmp_path / "t" / "modules.json", [_module("md_1", "tr_1")])
    _write(tmp_path / "t" / "rubric_criteria.json", [])

    content = load_content(tmp_path)

    assert "rubric_criterion" in content.raw_items
    assert validate_content(content) == []


def test_quizzes_plural_stem_maps_to_quiz(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "quizzes.json", [])

    assert "quiz" in load_content(tmp_path).raw_items


def test_items_json_and_per_type_files_are_merged(tmp_path: Path) -> None:
    _write(tmp_path / "items.json", {"track": [_track("tr_1")]})
    _write(tmp_path / "t" / "modules.json", [_module("md_1", "tr_1")])

    content = load_content(tmp_path)

    assert len(content.raw_items["track"]) == 1
    assert len(content.raw_items["module"]) == 1
    assert validate_content(content) == []


def test_non_json_files_and_mdx_dir_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "tracks.json", [_track("tr_1")])
    (tmp_path / "t" / "README.md").write_text("# notes", encoding="utf-8")
    _write(tmp_path / "mdx" / "whatever.json", {"not": "catalog"})

    content = load_content(tmp_path)

    assert list(content.raw_items) == ["track"]


def test_unknown_type_file_is_a_load_error(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "widgets.json", [])

    with pytest.raises(ContentLoadError, match=r"widgets\.json"):
        load_content(tmp_path)


def test_malformed_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "t" / "tracks.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ContentLoadError, match=r"t/tracks\.json is not valid JSON"):
        load_content(tmp_path)


def test_non_list_payload_is_a_load_error(tmp_path: Path) -> None:
    _write(tmp_path / "t" / "tracks.json", {"track": []})

    with pytest.raises(ContentLoadError, match="must be a list"):
        load_content(tmp_path)


def test_empty_root_is_a_load_error(tmp_path: Path) -> None:
    with pytest.raises(ContentLoadError, match=r"no items\.json or per-type"):
        load_content(tmp_path)


def test_cli_validate_loads_per_type_layout(tmp_path: Path, capsys: Any) -> None:
    _write(tmp_path / "t" / "tracks.json", [_track("tr_1")])

    assert main(["validate", "--content-root", str(tmp_path)]) == 0
    assert "content valid" in capsys.readouterr().out
