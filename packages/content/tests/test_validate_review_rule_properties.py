"""Property 11: the validator rejects exactly the unreviewed AI items (task 2.3).

**Validates: Requirements 6.1, 6.2**

For arbitrary origins and reviewer fields, ``validate_content`` reports an
``ai-reviewer-present`` failure for an item if and only if its origin is ``ai`` and
``reviewed_by`` or ``reviewed_at`` is missing, naming that item. The result depends only
on the content, not on the root it is read from.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from edu_it_content.validation import _RULE_REVIEWER, load_content, validate_content
from hypothesis import given, settings
from hypothesis import strategies as st

_REVIEWER = "usr_r"
_REVIEWED_AT = "2026-01-01T00:00:00+00:00"

# (origin, has reviewed_by, has reviewed_at) for each item.
_specs = st.lists(
    st.tuples(st.sampled_from(["ai", "human"]), st.booleans(), st.booleans()),
    min_size=0,
    max_size=8,
)


def _item(index: int, origin: str, has_by: bool, has_at: bool) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "stable_id": f"tr_{index}",
        "content_version": "cv_1",
        "origin": origin,
    }
    if has_by:
        provenance["reviewed_by"] = _REVIEWER
    if has_at:
        provenance["reviewed_at"] = _REVIEWED_AT
    return {
        "provenance": provenance,
        "title": {"fr": "Titre", "en": "Title"},
        "status": "published",
    }


def _failures(root: Path, items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    root.mkdir(parents=True)
    (root / "items.json").write_text(json.dumps({"track": items}), encoding="utf-8")
    return sorted((f.rule, f.stable_id) for f in validate_content(load_content(root)))


@settings(max_examples=50, deadline=None)
@given(specs=_specs)
def test_validator_rejects_exactly_unreviewed_ai_items(
    specs: list[tuple[str, bool, bool]],
) -> None:
    items = [_item(i, *spec) for i, spec in enumerate(specs)]
    expected = sorted(
        (_RULE_REVIEWER, f"tr_{i}")
        for i, (origin, has_by, has_at) in enumerate(specs)
        if origin == "ai" and not (has_by and has_at)
    )

    with tempfile.TemporaryDirectory() as tmp:
        first = _failures(Path(tmp) / "branch-a", items)
        second = _failures(Path(tmp) / "branch-b", items)

    assert first == expected
    assert second == first
