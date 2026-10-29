"""Smoke tests for ``edu-it content validate`` (task 10.1, Req 13.1-13.6).

These are a minimal confidence check that the CLI skeleton and the ordered
checks are wired together correctly:

- A tiny all-valid content root passes every check and the command exits ``0``
  (Req 13.1).
- An invalid content root (an ``origin == ai`` item with no reviewer) fails and
  the command exits non-zero, with the failure naming the failing rule and the
  offending item's ``stable_id`` (Req 13.1, 13.6).
- A content root that cannot be loaded exits with the distinct load-error status.

The per-check failing fixtures and the acyclicity property test are task 10.2;
this file deliberately stays minimal and does not attempt full per-rule coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from edu_it_content.cli import main


def _write_items(root: Path, items: dict[str, list[dict[str, Any]]]) -> None:
    """Write an ``items.json`` catalog into a content root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")


def _localized(fr: str = "Titre", en: str = "Title") -> dict[str, str]:
    return {"fr": fr, "en": en}


def _provenance(stable_id: str, *, origin: str = "human", reviewed: bool = True) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "stable_id": stable_id,
        "content_version": "cv_1",
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


def test_valid_content_exits_zero(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "content"
    _write_items(root, _valid_catalog())

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 0
    assert "content valid" in capsys.readouterr().out


def test_invalid_content_exits_nonzero_naming_rule_and_stable_id(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "content"
    catalog = _valid_catalog()
    # An AI-origin track with no recorded reviewer violates Req 13.6.
    catalog["track"] = [
        {
            "provenance": _provenance("tr_ai", origin="ai", reviewed=False),
            "title": _localized(),
            "status": "published",
        }
    ]
    # Point the module at the renamed track so only the reviewer rule fails.
    catalog["module"][0]["track_stable_id"] = "tr_ai"
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "ai-reviewer-present" in stderr
    assert "tr_ai" in stderr


def test_broken_reference_is_reported(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["module"][0]["track_stable_id"] = "tr_missing"
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "reference-resolution" in stderr
    assert "md_1" in stderr


def test_prerequisite_cycle_is_reported(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["prerequisite"] = [
        {"stable_id": "pr_1", "concept_stable_id": "cp_1", "requires_concept_stable_id": "cp_2"},
        {"stable_id": "pr_2", "concept_stable_id": "cp_2", "requires_concept_stable_id": "cp_1"},
    ]
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "prerequisite-acyclicity" in stderr


def test_missing_content_root_exits_load_error(tmp_path: Path, capsys: Any) -> None:
    missing = tmp_path / "does-not-exist"

    exit_code = main(["validate", "--content-root", str(missing)])

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err
