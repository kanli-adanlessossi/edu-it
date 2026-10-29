"""Property test for sync idempotence and completion preservation (task 11.2).

Pins Property 21 against the ``content sync`` core from task 11.1
(:func:`edu_it_content.sync.sync_content`) over the in-memory
:class:`~edu_it_content.content_store.In_Memory_ContentStore`:

- upsert by ``stable_id`` with no duplicate (Req 14.1),
- a second run over unchanged content changes nothing (Req 14.2),
- completion records survive every edit, removal and re-add (Req 14.3, 26.1),
- removed items are archived, never hard-deleted (Req 14.4).

Uses Hypothesis with at least 100 examples.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from edu_it_content.content_store import ContentStatus, In_Memory_ContentStore
from edu_it_content.sync import sync_content
from edu_it_content.validation import load_content
from hypothesis import given, settings
from hypothesis import strategies as st

_LESSON_POOL = 5

# One authored state: for each lesson index present, its edit variant (0..2).
_state = st.dictionaries(
    keys=st.integers(min_value=0, max_value=_LESSON_POOL - 1),
    values=st.integers(min_value=0, max_value=2),
)


def _catalog(state: dict[int, int]) -> dict[str, list[dict[str, Any]]]:
    """Build an all-valid catalog containing the lessons in ``state``."""

    def prov(stable_id: str, version: str) -> dict[str, Any]:
        return {
            "stable_id": stable_id,
            "content_version": version,
            "origin": "human",
            "reviewed_by": "usr_reviewer",
        }

    return {
        "track": [
            {
                "provenance": prov("tr_1", "cv_0"),
                "title": {"fr": "Titre", "en": "Title"},
                "status": "published",
            }
        ],
        "module": [
            {
                "provenance": prov("md_1", "cv_0"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": {"fr": "Titre", "en": "Title"},
            }
        ],
        "lesson": [
            {
                "provenance": prov(f"ls_{i}", f"cv_{variant}"),
                "module_stable_id": "md_1",
                "ordinal": i,
                "title": {"fr": f"Titre {variant}", "en": f"Title {variant}"},
            }
            for i, variant in sorted(state.items())
        ],
    }


def _sync(store: In_Memory_ContentStore, state: dict[int, int], root: Path) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(_catalog(state)), encoding="utf-8")
    return sync_content(store, load_content(root))


# Feature: learning-core, Property 21: Content sync is an idempotent upsert that
# preserves completions
# **Validates: Requirements 14.1, 14.2, 14.3, 14.4, 26.1**
@settings(max_examples=100, deadline=None)
@given(states=st.lists(_state, min_size=1, max_size=6))
def test_property_21_sync_is_idempotent_upsert_preserving_completions(
    states: list[dict[int, int]],
) -> None:
    store = In_Memory_ContentStore()
    ever_seen: set[str] = set()
    completions: set[tuple[str, str]] = set()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "content"
        for state in states:
            authored_ids = {"tr_1", "md_1"} | {f"ls_{i}" for i in state}
            previously_active = store.list_active_ids()

            report = _sync(store, state, root)

            # Upsert by stable_id: active set equals the authored set, and the
            # report never lists an id twice (no duplicate rows).
            assert store.list_active_ids() == authored_ids
            reported = report.inserted + report.updated + report.unchanged
            assert len(reported) == len(set(reported)) == len(authored_ids)
            # Removed items are archived, never hard-deleted.
            assert set(report.archived) == previously_active - authored_ids
            ever_seen |= authored_ids
            for stable_id in ever_seen:
                stored = store.get(stable_id)
                assert stored is not None
                expected = (
                    ContentStatus.ACTIVE if stable_id in authored_ids else ContentStatus.ARCHIVED
                )
                assert stored.status is expected

            # Second run over unchanged content is a no-op.
            writes_before = store.changed_upserts()
            snapshot = {sid: store.get(sid) for sid in ever_seen}
            again = _sync(store, state, root)
            assert not again.changed
            assert set(again.unchanged) == authored_ids
            assert store.changed_upserts() == writes_before
            assert {sid: store.get(sid) for sid in ever_seen} == snapshot

            # Plant completions for every known item; all must survive later syncs.
            for stable_id in sorted(ever_seen):
                store.seed_completion(stable_id, "lc_1")
                completions.add((stable_id, "lc_1"))
            assert completions <= store.completion_keys()

        assert completions <= store.completion_keys()
