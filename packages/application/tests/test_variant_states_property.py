"""Property test: variant states only move along the allowed transitions (ai-content task 15.3).

Feature: ai-content, Property 24: Variant states only move along the allowed transitions

**Validates: Requirements 13.3, 13.4, 15.4, 15.5, 15.6**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from edu_it_application.ai import RefillVariantPool, ReviewTaskActions, VerifyItem
from edu_it_domain.ai.lifecycle import (
    TRANSITIONS,
    InvalidTransitionError,
    VariantState,
    transition,
)
from edu_it_domain.ai.verification import LabUnderVerification, VerificationFinding
from edu_it_domain.ports.parent_questions import ParentQuestion
from edu_it_domain.ports.review_tasks import (
    NewReviewTask,
    TargetKind,
    TaskKind,
    TaskState,
)
from edu_it_domain.ports.role_assignment import ROLE_REVIEWER, RoleAssignment
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    Fake_SandboxProvider,
    In_Memory_ItemFlagRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_RoleAssignmentRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

S = VariantState
_T0 = datetime(2025, 1, 1, tzinfo=UTC)
_TRACK = "trk-a"
_PARENT = "q-parent"
_TERMINAL = (S.FLAGGED, S.REJECTED)


@settings(max_examples=100, deadline=None)
@given(
    start=st.sampled_from(list(S)),
    targets=st.lists(st.sampled_from(list(S)), max_size=25),
)
def test_any_event_sequence_only_follows_the_transition_table(
    start: VariantState, targets: list[VariantState]
) -> None:
    """Allowed events move along the table; any other is refused and changes nothing."""
    state = start
    for target in targets:
        before = state
        if target in TRANSITIONS[before]:
            state = transition(before, target)
            assert state is target
        else:
            with pytest.raises(InvalidTransitionError):
                transition(before, target)
            state = before
        if before in _TERMINAL:
            assert state is before  # Flagged and Rejected stay terminal
    # The only way back to Approved from outside PendingReview is the reinstatement.
    for source, targets_of in TRANSITIONS.items():
        if S.APPROVED in targets_of:
            assert source in (S.PENDING_REVIEW, S.WITHDRAWN)
    assert TRANSITIONS[S.FLAGGED] == frozenset()
    assert TRANSITIONS[S.REJECTED] == frozenset()


class _Parents:
    def get(self, stable_id: str) -> ParentQuestion | None:
        return _parent() if stable_id == _PARENT else None

    def approved_ids(self) -> list[str]:
        return [_PARENT]


class _Labs:
    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        return ()


def _parent() -> ParentQuestion:
    return ParentQuestion(
        stable_id=_PARENT,
        track_stable_id=_TRACK,
        concept_stable_id="c1",
        concept_title="Listing files",
        prompts={"en": "Which command shows directory contents?", "fr": "Quelle commande ?"},
        options=("ls", "rm"),
        approved=True,
    )


def _draft(n: int) -> str:
    return json.dumps(
        {
            "prompt_text": {"en": f"Which command lists files? ({n})", "fr": f"Liste ({n})"},
            "explanation": {"en": f"ls lists ({n})", "fr": f"ls liste ({n})"},
            "options": [
                {"text": {"en": "ls", "fr": "ls"}, "is_correct": True},
                {"text": {"en": "rm", "fr": "rm"}, "is_correct": False},
            ],
        }
    )


class _ScriptedLLM:
    def __init__(self, count: int) -> None:
        self._drafts = [_draft(n) for n in range(count)]

    def complete(self, prompt_version, input, schema, max_retries):  # type: ignore[no-untyped-def]
        from edu_it_domain.ports.llm_provider import AttemptUsage, StructuredOutput

        usage = AttemptUsage("scripted", 1, 1, 0, "ok")
        raw = self._drafts.pop(0)
        return StructuredOutput(schema.validate(raw), prompt_version, "scripted", (usage,))


@settings(max_examples=40, deadline=None)
@given(count=st.integers(min_value=1, max_value=3), agrees=st.booleans())
def test_refill_results_are_pending_with_one_task_or_flagged_with_findings(
    count: int, agrees: bool
) -> None:
    """Pass every check: PendingReview plus one task. Disagree: Flagged, findings, no task."""
    clock = Test_Clock(_T0)
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    verifier = VerifyItem(
        independent_llm=Fake_LLMProvider(
            {"verify_answer": json.dumps({"selected": [0] if agrees else [1]})}
        ),
        sandbox=Fake_SandboxProvider(clock),
        lab_verifier=_Labs(),
    )
    use_case = RefillVariantPool(
        llm=_ScriptedLLM(count),
        verifier=verifier,
        parents=_Parents(),
        variants=variants,
        tasks=tasks,
        flags=In_Memory_ItemFlagRepository(),
        clock=clock,
        ids=Sequential_IdGenerator("id"),
        glossary="[]",
        config=SimpleNamespace(VARIANT_POOL_THRESHOLD=count, VARIANTS_PER_REFILL_MAX=count),
    )

    result = use_case.execute(_PARENT)
    open_tasks = tasks.page([_TRACK], None, 50).items

    assert variants.count_approved(_PARENT) == 0  # nothing servable before review
    if agrees:
        assert len(result.pending) == count
        assert result.flagged == ()
        assert len(open_tasks) == count
        assert sorted(t.target_stable_id for t in open_tasks) == sorted(result.pending)
        for variant_id in result.pending:
            variant = variants.get(variant_id)
            assert variant is not None
            assert variant.state is S.PENDING_REVIEW
    else:
        assert result.pending == ()
        assert len(result.flagged) == count
        assert open_tasks == ()
        for variant_id in result.flagged:
            variant = variants.get(variant_id)
            assert variant is not None
            assert variant.state is S.FLAGGED
            assert result.findings[variant_id]


_COMMENT = st.text(alphabet="abc def", min_size=1, max_size=20).filter(lambda s: s.strip() != "")


@settings(max_examples=60, deadline=None)
@given(decision=st.sampled_from(["approve", "reject", "request_changes"]), comment=_COMMENT)
def test_review_decisions_set_state_reviewer_and_comment(decision: str, comment: str) -> None:
    """Approve and reject record reviewer and time; request changes keeps PendingReview."""
    clock = Test_Clock(_T0)
    variants = In_Memory_VariantRepository(clock)
    tasks = In_Memory_ReviewTaskRepository(clock, variants)
    roles = In_Memory_RoleAssignmentRepository()
    roles.set_role(RoleAssignment("rev", ROLE_REVIEWER, _TRACK))
    actions = ReviewTaskActions(
        tasks=tasks, variants=variants, roles=roles, clock=clock, ids=Sequential_IdGenerator("new")
    )
    variants.add(NewVariant("v1", "q", _TRACK, S.PENDING_REVIEW))
    variants.add_revision(NewRevision("v1", "c1", '{"en": {}}', origin="ai"))
    task = tasks.add(
        NewReviewTask(
            "t1",
            TaskKind.NEW_VARIANT,
            TargetKind.VARIANT,
            "v1",
            _TRACK,
            revision_content_version="c1",
        )
    )

    if decision == "approve":
        done = actions.approve("rev", task.stable_id)
        expected, rev_state, task_state = S.APPROVED, RevisionState.APPROVED, TaskState.APPROVED
    elif decision == "reject":
        done = actions.reject("rev", task.stable_id)
        expected, rev_state, task_state = S.REJECTED, RevisionState.REJECTED, TaskState.REJECTED
    else:
        done = actions.request_changes("rev", task.stable_id, comment)
        expected = S.PENDING_REVIEW
        rev_state, task_state = RevisionState.PENDING_REVIEW, TaskState.CHANGES_REQUESTED
        assert done.comment == comment.strip()

    variant = variants.get("v1")
    revision = variants.get_revision("v1", "c1")
    assert variant is not None
    assert revision is not None
    assert done.state is task_state
    assert variant.state is expected
    assert revision.state is rev_state
    if decision == "request_changes":
        assert revision.reviewed_by is None
    else:
        assert (revision.reviewed_by, revision.reviewed_at) == ("rev", _T0)
    # Every move made here is an allowed one.
    assert expected in TRANSITIONS[S.PENDING_REVIEW]
