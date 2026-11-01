"""Approval flow for the Linux Fundamentals Seed_Labs (task 19.3).

Covers Requirements 23.2-23.9 against a copy of the real repository content.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.lab_approval import LabApprovalError, approve_labs
from edu_it_content.sync import Approval, sync_content
from edu_it_content.validation import (
    apply_pending_allowance,
    load_content,
    load_pending_review_list,
    validate_content,
)

REPO = Path(__file__).resolve().parents[3]
SEED = REPO / "content" / "linux-fundamentals"
LABS = frozenset({"lab.fix-the-shared-folder", "lab.world-writable-entries"})
REVIEWER_RULE = "ai-reviewer-present"
APPROVAL = Approval(
    reviewer="kanli-adanlessossi", reviewed_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
)


def _copy_repo(tmp_path: Path, *, reviewed: bool) -> Path:
    """Copy the seed content; ``reviewed=False`` restores the pre-review state."""
    shutil.copytree(SEED, tmp_path / "content" / "linux-fundamentals")
    for image in ("linux-base", "python"):
        (tmp_path / "lab-images" / image).mkdir(parents=True)
    track = tmp_path / "content" / "linux-fundamentals"
    if not reviewed:
        labs = json.loads((track / "labs.json").read_text(encoding="utf-8"))
        for lab in labs:
            lab["provenance"].pop("reviewed_by", None)
            lab["provenance"].pop("reviewed_at", None)
        (track / "labs.json").write_text(json.dumps(labs), encoding="utf-8")
        (track / "pending-review.json").write_text(
            json.dumps({"stable_ids": sorted(LABS)}), encoding="utf-8"
        )
    return tmp_path / "content"


def _rules(root: Path, *, allow_pending: bool = False) -> list[str]:
    content = load_content(root)
    failures = validate_content(content)
    if allow_pending:
        failures = apply_pending_allowance(failures, content, load_pending_review_list(root))
    return [f.rule for f in failures]


class _FailingStore(In_Memory_ContentStore):
    def record_review(self, stable_id: str, reviewed_by: str, reviewed_at: datetime) -> None:
        raise OSError("store unavailable")


class _ForgetfulStore(In_Memory_ContentStore):
    def record_review(self, stable_id: str, reviewed_by: str, reviewed_at: datetime) -> None:
        return None  # accepts the write but never persists it


def test_repository_seed_labs_are_reviewed_and_pending_list_is_empty() -> None:
    assert load_pending_review_list(SEED.parent) == frozenset()
    content = load_content(SEED.parent)
    assert {lab["provenance"]["reviewed_by"] for lab in content.raw_items["lab"]} == {
        "kanli-adanlessossi"
    }
    assert _rules(SEED.parent) == []


def test_approval_records_reviewer_then_adds_labs_to_golden_set(tmp_path: Path) -> None:
    content = load_content(_copy_repo(tmp_path, reviewed=True))
    store = In_Memory_ContentStore()

    result = approve_labs(store, content, APPROVAL)

    assert result is not None and result.golden == LABS
    assert store.golden_ids() == LABS
    for stable_id in LABS:
        assert store.review_of(stable_id) == (APPROVAL.reviewer, APPROVAL.reviewed_at)
        assert store.get(stable_id) is not None


def test_failed_recording_adds_nothing_to_golden_set(tmp_path: Path) -> None:
    content = load_content(_copy_repo(tmp_path, reviewed=True))
    store = _FailingStore()
    with pytest.raises(LabApprovalError):
        approve_labs(store, content, APPROVAL)
    assert store.golden_ids() == frozenset()


def test_unconfirmed_readback_adds_nothing_to_golden_set(tmp_path: Path) -> None:
    content = load_content(_copy_repo(tmp_path, reviewed=True))
    store = _ForgetfulStore()
    with pytest.raises(LabApprovalError, match="read-back"):
        approve_labs(store, content, APPROVAL)
    assert store.golden_ids() == frozenset()


def test_reviewer_missing_from_provenance_adds_nothing(tmp_path: Path) -> None:
    content = load_content(_copy_repo(tmp_path, reviewed=False))
    store = In_Memory_ContentStore()
    with pytest.raises(LabApprovalError):
        approve_labs(store, content, APPROVAL)
    assert store.golden_ids() == frozenset()
    assert store.list_active_ids() == frozenset()


def test_rejection_records_nothing_and_syncs_nothing(tmp_path: Path) -> None:
    content = load_content(_copy_repo(tmp_path, reviewed=False))
    store = In_Memory_ContentStore()

    assert approve_labs(store, content, None) is None

    assert store.golden_ids() == frozenset()
    assert store.list_active_ids() == frozenset()
    assert all(store.review_of(stable_id) is None for stable_id in LABS)


def test_pre_review_fails_strictly_with_exactly_reviewer_rule_and_sync_refuses(
    tmp_path: Path,
) -> None:
    root = _copy_repo(tmp_path, reviewed=False)
    rules = _rules(root)
    assert rules == [REVIEWER_RULE] * len(LABS)
    assert _rules(root, allow_pending=True) == []

    store = In_Memory_ContentStore()
    with pytest.raises(ValueError, match=REVIEWER_RULE):
        sync_content(store, load_content(root))
    assert store.list_active_ids() == frozenset()


def test_after_review_and_empty_list_labs_validate_strictly_and_sync(tmp_path: Path) -> None:
    root = _copy_repo(tmp_path, reviewed=True)
    (root / "linux-fundamentals" / "pending-review.json").write_text(
        json.dumps({"stable_ids": []}), encoding="utf-8"
    )
    assert _rules(root) == []
    store = In_Memory_ContentStore()
    report = sync_content(store, load_content(root))
    assert set(report.inserted) >= LABS
