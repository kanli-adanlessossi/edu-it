"""Property 27: emptied pools fall back to the parent, flagged parents to another question.

Feature: ai-content, Property 27: Emptied pools fall back to the parent, and flagged
parents to another question

**Validates: Requirements 20.1, 20.3, 20.5**
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from edu_it_domain.ai.serving import (
    NoServableQuestionError,
    ServableVariant,
    resolve_question,
)
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord
from hypothesis import given, settings
from hypothesis import strategies as st

CONCEPT = "con-1"
DUE = datetime(2025, 1, 1, tzinfo=UTC)


def _question(stable_id: str) -> QuestionRecord:
    return QuestionRecord(
        stable_id,
        (QuestionOption(0, True), QuestionOption(1, False)),
        (CONCEPT,),
    )


PARENT = _question("q-parent")


def _variant(index: int) -> ServableVariant:
    return ServableVariant(f"var-{index:03d}", "v1", _question(PARENT.stable_id))


@settings(max_examples=100, deadline=None)
@given(
    parent_present=st.booleans(),
    parent_flagged=st.booleans(),
    pool_size=st.integers(min_value=0, max_value=5),
    alt_ids=st.sets(st.integers(min_value=0, max_value=6), max_size=4),
    include_parent_in_alts=st.booleans(),
    user_ref=st.text(min_size=1, max_size=8),
    pin=st.booleans(),
)
def test_pool_fallback_order(
    parent_present: bool,
    parent_flagged: bool,
    pool_size: int,
    alt_ids: set[int],
    include_parent_in_alts: bool,
    user_ref: str,
    pin: bool,
) -> None:
    """Variant, else parent, else another question, else no-servable; never a flagged parent."""
    pool = [_variant(i) for i in range(pool_size)]
    alternatives = [_question(f"q-alt-{i}") for i in sorted(alt_ids)]
    if include_parent_in_alts:
        alternatives.append(PARENT)
    parent = PARENT if parent_present else None
    pinned = pool[0] if pin and pool else None
    parent_servable = parent is not None and not parent_flagged
    others = [q for q in alternatives if parent is None or q.stable_id != PARENT.stable_id]

    def call() -> object:
        return resolve_question(
            concept_id=CONCEPT,
            parent=parent,
            parent_flagged=parent_flagged,
            pool=pool,
            alternatives=alternatives,
            user_ref=user_ref,
            due_at=DUE,
            pinned=pinned,
        )

    if parent_servable and pool:
        resolved = call()
        assert resolved.variant_ref in {v.ref for v in pool}  # type: ignore[attr-defined]
        if pinned is not None:
            assert resolved.variant_ref == pinned.ref  # type: ignore[attr-defined]
    elif parent_servable:
        resolved = call()
        assert resolved.question == PARENT  # type: ignore[attr-defined]
        assert resolved.variant_ref is None  # type: ignore[attr-defined]
    elif others:
        resolved = call()
        assert resolved.variant_ref is None  # type: ignore[attr-defined]
        assert resolved.question in others  # type: ignore[attr-defined]
        if parent is not None:
            assert resolved.question.stable_id != PARENT.stable_id  # type: ignore[attr-defined]
    else:
        with pytest.raises(NoServableQuestionError):
            call()
