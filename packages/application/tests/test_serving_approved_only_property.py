"""Property 26: serving returns only Approved items, from the moment of change.

*For any* set of items with arbitrary lifecycle states and flags, what the serving code
returns is a subset of the Approved or Served, unflagged items; and once an item is
Withdrawn or Flagged, every later serving request excludes it. A Rejected variant, or one
whose approved revision is not Approved, is never a candidate.

**Validates: Requirements 14.1, 14.2, 14.6, 22.2, 22.5, 22.6**
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from edu_it_application.ai import ServableCatalog
from edu_it_domain.ai.lifecycle import SERVABLE_STATES, VariantState
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import In_Memory_VariantRepository, Test_Clock
from edu_it_infrastructure.fakes.item_flags import In_Memory_ItemFlagRepository
from hypothesis import given, settings
from hypothesis import strategies as st

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
_CONCEPT = "con-1"
_PARENT = "q1"
_OTHER = "q2"
_DUES = [_T0 + timedelta(days=d) for d in range(15)]
_BODY = json.dumps({"options": [{"is_correct": True}, {"is_correct": False}]})


def _q(stable_id: str) -> QuestionRecord:
    return QuestionRecord(
        stable_id, (QuestionOption(1, True), QuestionOption(2, False)), (_CONCEPT,)
    )


class _Catalog:
    def __init__(self, questions: list[QuestionRecord]) -> None:
        self.questions = questions

    def get_module(self, stable_id: str) -> None:
        return None

    def list_modules_for_track(self, track_id: str) -> tuple[()]:
        return ()

    def get_concept(self, stable_id: str) -> None:
        return None

    def get_quiz(self, stable_id: str) -> QuizRecord:
        return QuizRecord(stable_id, "les", tuple(self.questions))

    def get_quiz_at_version(self, stable_id: str, content_version: str) -> QuizRecord:
        return self.get_quiz(stable_id)

    def list_questions_for_concept(self, concept_id: str) -> tuple[QuestionRecord, ...]:
        return tuple(sorted(self.questions, key=lambda q: q.stable_id))

    def get_rubric_for_project(self, project_id: str) -> None:
        return None


class _World:
    def __init__(self) -> None:
        self.variants = In_Memory_VariantRepository(Test_Clock(_T0))
        self.flags = In_Memory_ItemFlagRepository()
        self.catalog = ServableCatalog(
            _Catalog([_q(_PARENT), _q(_OTHER)]), self.flags, self.variants
        )

    def add(self, name: str, state: VariantState, revision_state: RevisionState) -> None:
        """Insert straight into the repository, whatever the state (arbitrary lifecycle)."""
        self.variants.add(NewVariant(name, _PARENT, "trk", state))
        self.variants.add_revision(NewRevision(name, "c1", _BODY, state=revision_state))
        stored = self.variants.get(name)
        assert stored is not None
        self.variants.save(replace(stored, approved_content_version="c1"))

    def flag(self, kind: ItemKind, stable_id: str) -> None:
        self.flags.flag(ItemKey(kind, stable_id), FlagReason(FlagReasonKind.REPORTS))

    def served_refs(self) -> set[str | None]:
        """Every variant reference the serving code returns across many requests."""
        return {
            self.catalog.resolve_review_question(
                concept_id=_CONCEPT,
                parent_question_id=_PARENT,
                user_ref=user,
                due_at=due,
            ).variant_ref
            for user in ("u1", "u2")
            for due in _DUES
        }


_scenario = st.lists(
    st.tuples(
        st.sampled_from(list(VariantState)),
        st.sampled_from(list(RevisionState)),
        st.booleans(),
    ),
    max_size=8,
)


@settings(max_examples=100, deadline=None)
@given(items=_scenario)
def test_served_variants_are_a_subset_of_approved_unflagged(
    items: list[tuple[VariantState, RevisionState, bool]],
) -> None:
    w = _World()
    allowed: set[str] = set()
    for i, (state, revision_state, flagged) in enumerate(items):
        name = f"v{i}"
        w.add(name, state, revision_state)
        if flagged:
            w.flag(ItemKind.VARIANT, name)
        if state in SERVABLE_STATES and revision_state is RevisionState.APPROVED and not flagged:
            allowed.add(f"{name}@c1")
    served = w.served_refs() - {None}
    assert served <= allowed
    if allowed:
        # An eligible pool always yields a variant, never a bypass to the parent.
        assert None not in w.served_refs()


@settings(max_examples=100, deadline=None)
@given(
    count=st.integers(min_value=1, max_value=6),
    data=st.data(),
)
def test_withdrawn_or_flagged_items_are_excluded_from_the_moment_of_change(
    count: int,
    data: st.DataObject,
) -> None:
    w = _World()
    names = [f"v{i}" for i in range(count)]
    for name in names:
        w.add(name, VariantState.SERVED, RevisionState.APPROVED)
    removed: set[str] = set()
    changes = data.draw(
        st.lists(
            st.tuples(st.sampled_from(names), st.sampled_from(["flag", "withdraw"])),
            max_size=2 * count,
        )
    )
    for name, how in changes:
        if how == "flag":
            w.flag(ItemKind.VARIANT, name)
        else:
            stored = w.variants.get(name)
            assert stored is not None
            if stored.state is VariantState.SERVED:
                w.variants.save(replace(stored, state=VariantState.WITHDRAWN))
        removed.add(name)
        refs = w.served_refs()
        assert not {f"{n}@c1" for n in removed} & refs
        if len(removed) == count:
            assert refs == {None}  # falls back to the parent, never to a removed item


@settings(max_examples=50, deadline=None)
@given(flagged=st.booleans())
def test_flagged_question_is_not_served_after_the_flag(flagged: bool) -> None:
    w = _World()
    w.add("v0", VariantState.SERVED, RevisionState.APPROVED)
    if flagged:
        w.flag(ItemKind.QUESTION, _PARENT)
    got = w.catalog.resolve_review_question(
        concept_id=_CONCEPT, parent_question_id=_PARENT, user_ref="u", due_at=_T0
    )
    if flagged:
        assert got.question.stable_id == _OTHER
        assert got.variant_ref is None
        assert [q.stable_id for q in w.catalog.list_questions_for_concept(_CONCEPT)] == [_OTHER]
    else:
        assert got.variant_ref == "v0@c1"
