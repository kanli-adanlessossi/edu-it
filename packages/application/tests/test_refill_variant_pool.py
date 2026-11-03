"""Unit tests for RefillVariantPool (ai-content task 15.1).

**Validates: Requirements 13.2, 13.3, 13.4, 13.5, 13.6, 13.7**
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from edu_it_application.ai import (
    RefillOutcome,
    RefillVariantPool,
    VariantDraftSchema,
    VerifyItem,
)
from edu_it_application.ai.review_tasks import revision_content_version
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ai.verification import FindingKind, LabUnderVerification, VerificationFinding
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    LLMProvider,
    MalformedOutputError,
    OutputSchema,
    PromptRef,
    ProviderUnavailableError,
    StructuredOutput,
)
from edu_it_domain.ports.parent_questions import ParentQuestion
from edu_it_domain.ports.variants import NewVariant, RevisionState
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    Fake_SandboxProvider,
    In_Memory_ItemFlagRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
_PARENT = "q-parent"
_TRACK = "track-1"


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
    """Returns each scripted raw text through the caller's schema, or raises it."""

    def __init__(self, script: list[str | Exception]) -> None:
        self._script = list(script)
        self.calls: list[tuple[PromptRef, LLMInput]] = []

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        self.calls.append((prompt_version, input))
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        usage = AttemptUsage("scripted", 1, 1, 0, "ok")
        return StructuredOutput(schema.validate(step), prompt_version, "scripted", (usage,))


class _Parents:
    def __init__(self, parent: ParentQuestion | None) -> None:
        self._parent = parent

    def get(self, stable_id: str) -> ParentQuestion | None:
        return self._parent if self._parent and self._parent.stable_id == stable_id else None

    def approved_ids(self) -> list[str]:
        return [self._parent.stable_id] if self._parent and self._parent.approved else []


class _Labs:
    def execute(self, lab: LabUnderVerification) -> tuple[VerificationFinding, ...]:
        return ()


def _parent(*, approved: bool = True) -> ParentQuestion:
    return ParentQuestion(
        stable_id=_PARENT,
        track_stable_id=_TRACK,
        concept_stable_id="c1",
        concept_title="Listing files",
        prompts={"en": "Which command shows directory contents?", "fr": "Quelle commande ?"},
        options=("ls", "rm"),
        approved=approved,
    )


class _World:
    def __init__(
        self,
        script: list[str | Exception],
        *,
        parent: ParentQuestion | None = None,
        answer: list[int] | None = None,
        config: object | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self.clock = Test_Clock(_T0)
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.flags = In_Memory_ItemFlagRepository()
        self.llm = _ScriptedLLM(script)
        independent = Fake_LLMProvider(
            {"verify_answer": json.dumps({"selected": [0] if answer is None else answer})}
        )
        verifier = VerifyItem(
            independent_llm=independent,
            sandbox=Fake_SandboxProvider(self.clock),
            lab_verifier=_Labs(),
        )
        kwargs = {} if config is None else {"config": config}
        self.use_case = RefillVariantPool(
            llm=llm if llm is not None else self.llm,
            verifier=verifier,
            parents=_Parents(parent if parent is not None else _parent()),
            variants=self.variants,
            tasks=self.tasks,
            flags=self.flags,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            glossary="[]",
            **kwargs,
        )

    def open_tasks(self) -> tuple[object, ...]:
        return self.tasks.page([_TRACK], None, 50).items


def test_verified_variant_is_pending_review_with_one_review_task() -> None:
    world = _World([_draft(1), _draft(2), _draft(3)])

    result = world.use_case.execute(_PARENT)

    assert result.outcome is RefillOutcome.COMPLETED
    assert len(result.pending) == 3
    assert result.flagged == ()
    assert len(world.open_tasks()) == 3
    for variant_id in result.pending:
        variant = world.variants.get(variant_id)
        assert variant is not None
        assert variant.state is VariantState.PENDING_REVIEW
        assert variant.parent_question_stable_id == _PARENT
        assert variant.approved_content_version is None
    # Nothing is servable before a reviewer approves.
    assert world.variants.count_approved(_PARENT) == 0
    assert world.variants.list_servable(_PARENT) == []


def test_revision_carries_provenance_and_no_reviewer() -> None:
    world = _World(
        [_draft(1)], config=SimpleNamespace(VARIANT_POOL_THRESHOLD=1, VARIANTS_PER_REFILL_MAX=1)
    )

    result = world.use_case.execute(_PARENT)

    task = world.open_tasks()[0]
    revision = world.variants.get_revision(result.pending[0], task.revision_content_version)  # type: ignore[attr-defined]
    assert revision is not None
    assert revision.state is RevisionState.PENDING_REVIEW
    assert (revision.origin, revision.model, revision.prompt_id, revision.prompt_version) == (
        "ai",
        "scripted",
        "variant",
        "1",
    )
    assert revision.generation_date == _T0
    assert revision.reviewed_by is None
    assert revision.reviewed_at is None
    assert json.loads(revision.body_json)["parent_question_stable_id"] == _PARENT


def test_disagreement_flags_the_variant_and_stores_the_finding_without_a_task() -> None:
    world = _World(
        [_draft(1)],
        answer=[1],
        config=SimpleNamespace(VARIANT_POOL_THRESHOLD=1, VARIANTS_PER_REFILL_MAX=1),
    )

    result = world.use_case.execute(_PARENT)

    assert result.pending == ()
    (variant_id,) = result.flagged
    variant = world.variants.get(variant_id)
    assert variant is not None
    assert variant.state is VariantState.FLAGGED
    assert [f.kind for f in result.findings[variant_id]] == [FindingKind.DISAGREEMENT]
    assert world.open_tasks() == ()
    stored = world.variants.get_revision(variant_id, _version(_draft(1)))
    assert stored is not None
    assert json.loads(stored.findings_json or "[]")[0]["kind"] == "disagreement"
    assert world.variants.count_approved(_PARENT) == 0


def test_variant_duplicating_the_parent_is_flagged() -> None:
    same = json.loads(_draft(1))
    same["prompt_text"]["en"] = "Which command shows directory contents?"
    world = _World(
        [json.dumps(same)],
        config=SimpleNamespace(VARIANT_POOL_THRESHOLD=1, VARIANTS_PER_REFILL_MAX=1),
    )

    result = world.use_case.execute(_PARENT)

    (variant_id,) = result.flagged
    assert FindingKind.DUPLICATE in {f.kind for f in result.findings[variant_id]}


def test_unapproved_parent_creates_no_job() -> None:
    world = _World([_draft(1)], parent=_parent(approved=False))

    assert world.use_case.execute(_PARENT).outcome is RefillOutcome.NOT_APPROVED
    assert world.llm.calls == []
    assert world.open_tasks() == ()


def test_unknown_and_flagged_parents_create_no_job() -> None:
    world = _World([_draft(1)])
    assert world.use_case.execute("missing").outcome is RefillOutcome.NOT_APPROVED

    world.flags.flag(ItemKey(ItemKind.QUESTION, _PARENT), FlagReason(FlagReasonKind.REPORTS))
    assert world.use_case.execute(_PARENT).outcome is RefillOutcome.NOT_APPROVED
    assert world.llm.calls == []


def test_shortfall_is_capped_by_variants_per_refill_max() -> None:
    config = SimpleNamespace(VARIANT_POOL_THRESHOLD=5, VARIANTS_PER_REFILL_MAX=2)
    world = _World([_draft(1), _draft(2), _draft(3)], config=config)

    result = world.use_case.execute(_PARENT)

    assert len(result.pending) == 2
    assert len(world.llm.calls) == 2


def test_full_pool_makes_no_model_call() -> None:
    world = _World([_draft(1)])
    for n in range(3):
        world.variants.add(NewVariant(f"a{n}", _PARENT, _TRACK, state=VariantState.APPROVED))

    assert world.use_case.execute(_PARENT).outcome is RefillOutcome.POOL_FULL
    assert world.llm.calls == []


def test_request_carries_only_allowlisted_fields_and_no_key() -> None:
    world = _World(
        [_draft(1)], config=SimpleNamespace(VARIANT_POOL_THRESHOLD=1, VARIANTS_PER_REFILL_MAX=1)
    )

    world.use_case.execute(_PARENT)

    ((prompt, request),) = world.llm.calls
    assert prompt == PromptRef("variant", "1")
    assert request.kind == "variant"
    assert set(request.fields) == {
        "parent_question",
        "parent_options",
        "concept",
        "locale",
        "glossary",
        "count",
    }


@pytest.mark.parametrize(
    "stop",
    [
        MalformedOutputError("bad"),
        ProviderUnavailableError("down"),
        BudgetReachedError("daily"),
    ],
)
def test_a_stop_creates_nothing_servable_and_raises_nothing(stop: Exception) -> None:
    world = _World([stop])

    result = world.use_case.execute(_PARENT)

    assert result.outcome is RefillOutcome.STOPPED
    assert result.stop_reason == type(stop).__name__
    assert world.variants.parents_below(10) == []
    assert world.variants.count_approved(_PARENT) == 0
    assert world.open_tasks() == ()
    assert len(world.llm.calls) == 1


def test_a_stop_after_a_success_keeps_the_earlier_variant_pending_and_stops_the_run() -> None:
    world = _World([_draft(1), BudgetReachedError("job"), _draft(3)])

    result = world.use_case.execute(_PARENT)

    assert result.outcome is RefillOutcome.STOPPED
    assert len(result.pending) == 1
    assert len(world.llm.calls) == 2  # no further attempt after the stop
    assert world.variants.count_approved(_PARENT) == 0


def test_malformed_raw_output_through_the_real_schema_stops_the_run() -> None:
    world = _World([], llm=Fake_LLMProvider({"variant": '{"prompt_text": "no"}'}))

    result = world.use_case.execute(_PARENT)

    assert result.outcome is RefillOutcome.STOPPED
    assert result.stop_reason == "MalformedOutputError"
    assert world.variants.parents_below(10) == []


def _version(raw: str) -> str:
    draft = json.loads(raw)
    return revision_content_version(
        {
            "parent_question_stable_id": _PARENT,
            "prompt_text": draft["prompt_text"],
            "options": draft["options"],
            "explanation": draft["explanation"],
        }
    )


def test_draft_schema_rejects_missing_correct_option_and_missing_locale() -> None:
    schema = VariantDraftSchema()
    no_correct: Mapping[str, object] = json.loads(_draft(1))
    no_correct["options"][0]["is_correct"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="correct"):
        schema.validate(json.dumps(no_correct))
    no_fr = json.loads(_draft(1))
    del no_fr["explanation"]["fr"]
    with pytest.raises(ValueError, match=r"explanation\.fr"):
        schema.validate(json.dumps(no_fr))
    assert schema.validate(_draft(1))["prompt_text"]["en"].startswith("Which")  # type: ignore[index]
