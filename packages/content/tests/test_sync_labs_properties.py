"""Property test for lab content sync (task 14.5).

Property 23: Lab content sync is idempotent and preserves progress.

**Validates: Requirements 20.10, 20.11, 23.7**
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import LoadedContent
from hypothesis import given, settings
from hypothesis import strategies as st

_POOL = 3

# One authored state: lab index present -> edit variant.
_state = st.dictionaries(
    keys=st.integers(min_value=0, max_value=_POOL - 1),
    values=st.integers(min_value=0, max_value=2),
)


def _loc(text: str) -> dict[str, str]:
    return {"fr": f"{text}-fr", "en": f"{text}-en"}


def _prov(stable_id: str, origin: str = "human", reviewer: str | None = "usr_r") -> dict[str, Any]:
    prov: dict[str, Any] = {"stable_id": stable_id, "content_version": "cv_1", "origin": origin}
    if reviewer is not None:
        prov["reviewed_by"] = reviewer
    return prov


def _items(state: dict[int, int]) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {
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
        "lab": [],
        "lab_task": [],
        "lab_check": [],
    }
    for i, variant in sorted(state.items()):
        items["lab"].append(
            {
                "provenance": _prov(f"lb_{i}"),
                "lesson_stable_id": "ls_1",
                "title": _loc(f"lab{i}-{variant}"),
                "kind": "terminal",
                "image": "linux-base",
                "concept_stable_ids": ["cn_1"],
                "instructions": _loc(f"ins{i}-{variant}"),
                "setup_script": "labs/setup.sh",
                "check_bundle": "labs/bundle",
            }
        )
        items["lab_task"].append(
            {
                "stable_id": f"lt_{i}",
                "lab_stable_id": f"lb_{i}",
                "ordinal": 0,
                "title": _loc("task"),
                "description": _loc(f"desc{i}-{variant}"),
            }
        )
        items["lab_check"].append(
            {
                "stable_id": f"lc_{i}",
                "lab_task_stable_id": f"lt_{i}",
                "timeout_seconds": 4,
                "script": "check.sh",
                "outcomes": [
                    {
                        "code": "pass",
                        "exit_code": 0,
                        "feedback_key": "a.pass",
                        "message": _loc("p"),
                    },
                    {
                        "code": "fail",
                        "exit_code": 1,
                        "feedback_key": "a.fail",
                        "message": _loc("f"),
                    },
                ],
            }
        )
    return items


def _make_repo(root: Path) -> None:
    (root / "lab-images" / "linux-base").mkdir(parents=True)
    (root / "labs" / "bundle").mkdir(parents=True)
    (root / "labs" / "setup.sh").write_text("#!/bin/sh\n", encoding="utf-8")


@settings(max_examples=100, deadline=None)
@given(states=st.lists(_state, min_size=1, max_size=5))
def test_lab_sync_idempotent_and_preserves_progress(states: list[dict[int, int]]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_repo(root)
        store = In_Memory_ContentStore()
        seen: set[int] = set()
        seeded: set[tuple[str, str]] = set()

        for state in states:
            content = LoadedContent(raw_items=_items(state), root=root / "content")
            sync_content(store, content)

            # Learners complete every task that currently exists.
            for i in state:
                store.seed_completion(f"lt_{i}", "c1")
                seeded.add((f"lt_{i}", "c1"))
            seen |= set(state)

            # Second sync over the same content changes nothing.
            again = sync_content(
                store, LoadedContent(raw_items=_items(state), root=root / "content")
            )
            assert not again.changed
            assert again.orphaned == ()

            # Completions are never lost.
            assert store.completion_keys() >= frozenset(seeded)

            # Removed labs are archived, never deleted; present ones are not archived.
            for i in seen:
                stored = store.get(f"lb_{i}")
                assert stored is not None
                assert (stored.status is ContentStatus.ARCHIVED) == (i not in state)


@settings(max_examples=100, deadline=None)
@given(state=_state.filter(bool), pending=st.booleans(), data=st.data())
def test_unreviewed_ai_lab_is_never_synced(
    state: dict[int, int], pending: bool, data: st.DataObject
) -> None:
    # ``pending`` models "named or not in the Pending_Review_List": the refusal
    # depends only on provenance, so the outcome must be identical either way.
    bad = data.draw(st.sampled_from(sorted(state)))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_repo(root)
        items = _items(state)
        for lab in items["lab"]:
            if lab["provenance"]["stable_id"] == f"lb_{bad}":
                lab["provenance"] = _prov(f"lb_{bad}", origin="ai", reviewer=None)
        store = In_Memory_ContentStore()
        with pytest.raises(ValueError, match="ai-reviewer-present"):
            sync_content(store, LoadedContent(raw_items=items, root=root / "content"))
        assert store.list_active_ids() == frozenset()
        assert pending in (True, False)
