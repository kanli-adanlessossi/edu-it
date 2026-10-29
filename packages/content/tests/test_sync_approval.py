"""Tests for approval attribution, golden-set marking, and rejection (task 16.2).

Covers Req 16.2 (the approving user is recorded as ``reviewed_by`` /
``reviewed_at``), 16.3 (approved content is marked the ``Golden_Set``), and 16.4
(without approval: no reviewer recorded, no golden set marked, and unreviewed AI
content is neither synced nor served). Both the library path
(:func:`edu_it_content.sync.sync_content`) and the CLI path
(``edu-it content sync --approved-by/--approved-at``) are exercised.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from edu_it_content import cli
from edu_it_content.cli import main
from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import Approval, sync_content
from edu_it_content.validation import LoadedContent, load_content

_APPROVED_AT = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)

#: Items carrying a provenance record (so a reviewer can be attributed to them).
_PROVENANCED = frozenset({"tr_1", "md_1", "ls_1", "cp_1", "cp_2"})
#: Every authored item, including the provenance-less prerequisite edge.
_ALL_IDS = _PROVENANCED | {"pr_1"}


def _localized() -> dict[str, str]:
    return {"fr": "Titre", "en": "Title"}


def _provenance(stable_id: str, *, origin: str) -> dict[str, Any]:
    """AI items are authored with no reviewer, like the unapproved seed content."""
    provenance: dict[str, Any] = {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": origin,
    }
    if origin != "ai":
        provenance["reviewed_by"] = "usr_author"
    return provenance


def _catalog(origin: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "track": [
            {
                "provenance": _provenance("tr_1", origin=origin),
                "title": _localized(),
                "status": "published",
            }
        ],
        "module": [
            {
                "provenance": _provenance("md_1", origin=origin),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "lesson": [
            {
                "provenance": _provenance("ls_1", origin=origin),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "concept": [
            {"provenance": _provenance("cp_1", origin=origin), "title": _localized()},
            {"provenance": _provenance("cp_2", origin=origin), "title": _localized()},
        ],
        "prerequisite": [
            {
                "stable_id": "pr_1",
                "concept_stable_id": "cp_2",
                "requires_concept_stable_id": "cp_1",
            }
        ],
    }


def _write(tmp_path: Path, origin: str) -> Path:
    root = tmp_path / "content"
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(_catalog(origin)), encoding="utf-8")
    return root


def _load(tmp_path: Path, origin: str) -> LoadedContent:
    return load_content(_write(tmp_path, origin))


# -- Library path ------------------------------------------------------------


def test_approval_records_reviewer_on_every_provenanced_item(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()

    sync_content(
        store,
        _load(tmp_path, "ai"),
        approval=Approval(reviewer="usr_expert", reviewed_at=_APPROVED_AT),
    )

    for stable_id in _PROVENANCED:
        assert store.review_of(stable_id) == ("usr_expert", _APPROVED_AT)
    # The prerequisite edge has no provenance, so no reviewer is attributed to it.
    assert store.review_of("pr_1") is None


def test_approval_marks_all_synced_content_golden(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()

    sync_content(
        store,
        _load(tmp_path, "ai"),
        approval=Approval(reviewer="usr_expert", reviewed_at=_APPROVED_AT),
    )

    assert store.golden_ids() == _ALL_IDS
    assert store.list_active_ids() == _ALL_IDS


def test_approval_makes_unreviewed_ai_content_syncable(tmp_path: Path) -> None:
    """Unreviewed AI content fails validation alone but passes once approved."""
    with pytest.raises(ValueError, match="cannot sync invalid content"):
        sync_content(In_Memory_ContentStore(), _load(tmp_path, "ai"))

    store = In_Memory_ContentStore()
    report = sync_content(
        store,
        _load(tmp_path, "ai"),
        approval=Approval(reviewer="usr_expert", reviewed_at=_APPROVED_AT),
    )
    assert set(report.inserted) == _ALL_IDS


def test_approving_after_sync_does_not_count_as_content_change(tmp_path: Path) -> None:
    """Review attribution is not part of the canonical payload (no update, no new version)."""
    store = In_Memory_ContentStore()
    sync_content(store, _load(tmp_path, "human"))

    report = sync_content(
        store,
        _load(tmp_path, "human"),
        approval=Approval(reviewer="usr_expert", reviewed_at=_APPROVED_AT),
    )

    assert report.updated == ()
    assert set(report.unchanged) == _ALL_IDS
    assert store.golden_ids() == _ALL_IDS
    assert store.review_of("tr_1") == ("usr_expert", _APPROVED_AT)


def test_no_approval_records_no_reviewer_and_no_golden_set(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()

    sync_content(store, _load(tmp_path, "human"))

    assert store.golden_ids() == frozenset()
    assert all(store.review_of(stable_id) is None for stable_id in _ALL_IDS)


def test_rejection_of_unreviewed_ai_content_syncs_and_serves_nothing(tmp_path: Path) -> None:
    store = In_Memory_ContentStore()

    with pytest.raises(ValueError, match="cannot sync invalid content"):
        sync_content(store, _load(tmp_path, "ai"))

    assert store.list_active_ids() == frozenset()
    assert store.golden_ids() == frozenset()
    assert all(store.review_of(stable_id) is None for stable_id in _ALL_IDS)
    assert all(store.get(stable_id) is None for stable_id in _ALL_IDS)


def test_blank_reviewer_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty reviewer"):
        Approval(reviewer="   ", reviewed_at=_APPROVED_AT)


# -- CLI path ----------------------------------------------------------------


@pytest.fixture
def captured_store(monkeypatch: pytest.MonkeyPatch) -> list[In_Memory_ContentStore]:
    """Capture the store the CLI constructs so tests can inspect it."""
    stores: list[In_Memory_ContentStore] = []

    class _Capturing(In_Memory_ContentStore):
        def __init__(self) -> None:
            super().__init__()
            stores.append(self)

    monkeypatch.setattr(cli, "In_Memory_ContentStore", _Capturing)
    return stores


def test_cli_approved_by_records_reviewer_and_marks_golden(
    tmp_path: Path,
    captured_store: list[In_Memory_ContentStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _write(tmp_path, "ai")

    code = main(
        [
            "sync",
            "--content-root",
            str(root),
            "--approved-by",
            "usr_expert",
            "--approved-at",
            "2025-01-02T03:04:05+00:00",
        ]
    )

    assert code == 0
    (store,) = captured_store
    assert store.review_of("tr_1") == ("usr_expert", _APPROVED_AT)
    assert store.golden_ids() == _ALL_IDS
    assert f"approved by usr_expert: {len(_ALL_IDS)} item(s) in the Golden_Set" in (
        capsys.readouterr().out
    )


def test_cli_approved_at_defaults_to_now_utc(
    tmp_path: Path, captured_store: list[In_Memory_ContentStore]
) -> None:
    before = datetime.now(UTC)
    code = main(["sync", "--content-root", str(_write(tmp_path, "ai")), "--approved-by", "usr_x"])
    after = datetime.now(UTC)

    assert code == 0
    review = captured_store[0].review_of("tr_1")
    assert review is not None
    reviewer, reviewed_at = review
    assert reviewer == "usr_x"
    assert before <= reviewed_at <= after


def test_cli_approved_at_accepts_non_utc_offset(
    tmp_path: Path, captured_store: list[In_Memory_ContentStore]
) -> None:
    code = main(
        [
            "sync",
            "--content-root",
            str(_write(tmp_path, "ai")),
            "--approved-by",
            "usr_x",
            "--approved-at",
            "2025-01-02T04:04:05+01:00",
        ]
    )

    assert code == 0
    review = captured_store[0].review_of("tr_1")
    assert review is not None
    assert review[1] == datetime(2025, 1, 2, 4, 4, 5, tzinfo=timezone(timedelta(hours=1)))
    assert review[1] == _APPROVED_AT


def test_cli_without_approved_by_marks_nothing(
    tmp_path: Path,
    captured_store: list[In_Memory_ContentStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["sync", "--content-root", str(_write(tmp_path, "human"))])

    assert code == 0
    (store,) = captured_store
    assert store.golden_ids() == frozenset()
    assert store.review_of("tr_1") is None
    assert "Golden_Set" not in capsys.readouterr().out


def test_cli_rejection_of_unreviewed_ai_content_syncs_nothing(
    tmp_path: Path,
    captured_store: list[In_Memory_ContentStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["sync", "--content-root", str(_write(tmp_path, "ai"))])

    assert code == 1
    (store,) = captured_store
    assert store.list_active_ids() == frozenset()
    assert store.golden_ids() == frozenset()
    assert "cannot sync invalid content" in capsys.readouterr().err


def test_cli_approved_at_without_approved_by_exits_2(
    tmp_path: Path,
    captured_store: list[In_Memory_ContentStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "sync",
            "--content-root",
            str(_write(tmp_path, "ai")),
            "--approved-at",
            "2025-01-02T03:04:05+00:00",
        ]
    )

    assert code == 2
    assert "--approved-at requires --approved-by" in capsys.readouterr().err
    assert captured_store == []  # failed before any store was built or written


def test_cli_naive_approved_at_exits_2(
    tmp_path: Path,
    captured_store: list[In_Memory_ContentStore],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "sync",
            "--content-root",
            str(_write(tmp_path, "ai")),
            "--approved-by",
            "usr_expert",
            "--approved-at",
            "2025-01-02T03:04:05",
        ]
    )

    assert code == 2
    assert "must include a timezone offset" in capsys.readouterr().err
    assert captured_store == []


def test_cli_malformed_approved_at_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "sync",
            "--content-root",
            str(_write(tmp_path, "ai")),
            "--approved-by",
            "usr_expert",
            "--approved-at",
            "not-a-date",
        ]
    )

    assert code == 2
    assert "not a valid ISO 8601 timestamp" in capsys.readouterr().err


def test_cli_blank_approved_by_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["sync", "--content-root", str(_write(tmp_path, "ai")), "--approved-by", "  "])

    assert code == 2
    assert "non-empty reviewer" in capsys.readouterr().err
