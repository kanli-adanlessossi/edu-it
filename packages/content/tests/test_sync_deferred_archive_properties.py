"""Property test for deferred archiving during active use (task 11.4).

Pins Property 25 against ``sync_content`` and ``In_Memory_ContentStore``
(task 11.3): a removed item with an active session is never archived (stays
ACTIVE and is reported deferred), is archived by the first sync after all its
sessions complete, and is never hard-deleted.

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

_POOL = 4


def _catalog(lessons: set[int]) -> dict[str, list[dict[str, Any]]]:
    def prov(stable_id: str) -> dict[str, Any]:
        return {
            "stable_id": stable_id,
            "content_version": "cv_1",
            "origin": "human",
            "reviewed_by": "usr_reviewer",
        }

    title = {"fr": "Titre", "en": "Title"}
    return {
        "track": [{"provenance": prov("tr_1"), "title": title, "status": "published"}],
        "module": [
            {"provenance": prov("md_1"), "track_stable_id": "tr_1", "ordinal": 0, "title": title}
        ],
        "lesson": [
            {
                "provenance": prov(f"ls_{i}"),
                "module_stable_id": "md_1",
                "ordinal": i,
                "title": title,
            }
            for i in sorted(lessons)
        ],
    }


def _sync(store: In_Memory_ContentStore, lessons: set[int], root: Path) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(_catalog(lessons)), encoding="utf-8")
    return sync_content(store, load_content(root))


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[set[int], dict[int, int], list[tuple[int, int]]]:
    removed = draw(st.sets(st.integers(0, _POOL - 1), max_size=_POOL))
    sessions = {i: draw(st.integers(0, 3)) for i in range(_POOL)}
    all_sessions = [(i, k) for i, n in sessions.items() for k in range(n)]
    order = draw(st.permutations(all_sessions))
    return removed, sessions, list(order)


# Feature: learning-core, Property 25: Removed items are archived only after
# active sessions complete
# **Validates: Requirements 26.2**
@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_property_25_removed_items_archived_only_after_sessions_complete(
    scenario: tuple[set[int], dict[int, int], list[tuple[int, int]]],
) -> None:
    removed, sessions, order = scenario
    all_lessons = set(range(_POOL))
    store = In_Memory_ContentStore()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "content"
        _sync(store, all_lessons, root)
        for i, n in sessions.items():
            for k in range(n):
                store.start_session(f"s_{i}_{k}", f"ls_{i}")

        remaining = all_lessons - removed
        open_sessions = {i: sessions[i] for i in range(_POOL)}
        archived_seen: set[str] = set()

        def check(report: Any) -> None:
            for i in removed:
                sid = f"ls_{i}"
                stored = store.get(sid)
                assert stored is not None  # never hard-deleted
                if open_sessions[i] > 0:
                    assert stored.status is ContentStatus.ACTIVE
                    assert sid in report.deferred
                    assert sid not in report.archived
                else:
                    assert stored.status is ContentStatus.ARCHIVED
                    assert sid not in report.deferred
                    if sid not in archived_seen:
                        # first sync after all sessions complete archives it
                        assert sid in report.archived
                        archived_seen.add(sid)
            for i in remaining:
                stored = store.get(f"ls_{i}")
                assert stored is not None
                assert stored.status is ContentStatus.ACTIVE

        check(_sync(store, remaining, root))
        for i, k in order:
            store.complete_session(f"s_{i}_{k}")
            open_sessions[i] -= 1
            check(_sync(store, remaining, root))
