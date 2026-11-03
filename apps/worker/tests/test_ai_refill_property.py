"""Property 25: refill is triggered by the threshold, once per cycle, and failure serves nothing.

Run against the in-memory ports and a scripted model. No model is called and no network is used.

**Validates: Requirements 13.1, 13.6, 13.7, 13.8, 13.9**
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from edu_it_domain import config as cfg
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.ports.catalog import QuizRecord
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
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.composition import CompositionSettings
from edu_it_infrastructure.fakes import (
    Fake_LLMProvider,
    Fake_SandboxProvider,
    In_Memory_GenerationLedger,
    In_Memory_ItemFlagRepository,
    In_Memory_ReviewTaskRepository,
    In_Memory_VariantRepository,
    Sequential_IdGenerator,
    Test_Clock,
)
from edu_it_infrastructure.fakes.task_queue import In_Memory_TaskQueue
from edu_it_infrastructure.llm.ledger_binding import JobScope
from edu_it_worker.ai.composition import build_ai_worker
from edu_it_worker.ai.jobs import (
    REFILL_PARENT_JOB,
    ScanRefillParents,
)
from hypothesis import given, settings
from hypothesis import strategies as st

T0 = datetime(2025, 1, 1, tzinfo=UTC)
TRACK = "track-1"
THRESHOLD = cfg.VARIANT_POOL_THRESHOLD
CAP = cfg.VARIANTS_PER_REFILL_MAX


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
    def __init__(self, script: list[str | Exception]) -> None:
        self._script = list(script)

    def complete(
        self, prompt_version: PromptRef, input: LLMInput, schema: OutputSchema, max_retries: int
    ) -> StructuredOutput:
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        usage = AttemptUsage("scripted", 1, 1, 0, "ok")
        return StructuredOutput(schema.validate(step), prompt_version, "scripted", (usage,))


class _Parents:
    def __init__(self, *parents: ParentQuestion) -> None:
        self._parents = {p.stable_id: p for p in parents}

    def get(self, stable_id: str) -> ParentQuestion | None:
        return self._parents.get(stable_id)

    def approved_ids(self) -> list[str]:
        return sorted(p.stable_id for p in self._parents.values() if p.approved)


def _parent(stable_id: str, *, approved: bool = True) -> ParentQuestion:
    return ParentQuestion(
        stable_id=stable_id,
        track_stable_id=TRACK,
        concept_stable_id="con-1",
        concept_title="Listing files",
        prompts={"en": "Which command shows directory contents?", "fr": "Quelle commande ?"},
        options=("ls", "rm"),
        approved=approved,
    )


class _Catalog:
    def get_module(self, stable_id: str) -> None:
        return None

    def list_modules_for_track(self, track_id: str) -> tuple[()]:
        return ()

    def get_concept(self, stable_id: str) -> None:
        return None

    def get_quiz(self, stable_id: str) -> QuizRecord:
        return QuizRecord(stable_id, "les", ())

    def get_quiz_at_version(self, stable_id: str, content_version: str) -> QuizRecord:
        return self.get_quiz(stable_id)

    def list_questions_for_concept(self, concept_id: str) -> tuple[()]:
        return ()

    def get_rubric_for_project(self, project_id: str) -> None:
        return None


class _World:
    def __init__(self, parents: _Parents, script: list[str | Exception] | None = None) -> None:
        self.clock = Test_Clock(T0)
        self.queue = In_Memory_TaskQueue()
        self.ledger = In_Memory_GenerationLedger(self.clock)
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.flags = In_Memory_ItemFlagRepository()
        self.parents = parents
        self.llm = _ScriptedLLM(script or [])
        self.worker = build_ai_worker(
            queue=self.queue,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            ledger=self.ledger,
            parents=parents,
            variants=self.variants,
            review_tasks=self.tasks,
            flags=self.flags,
            catalog=_Catalog(),
            sandbox=Fake_SandboxProvider(self.clock),
            settings=CompositionSettings(),
            wire=self._wire,
        )

    def _wire(self, scope: JobScope) -> tuple[LLMProvider, LLMProvider]:
        answering = Fake_LLMProvider({"verify_answer": json.dumps({"selected": [0]})})
        return self.llm, answering

    def approve(self, parent: str, count: int) -> None:
        for index in range(count):
            name = f"{parent}-v{index}"
            self.variants.add(NewVariant(name, parent, TRACK, VariantState.APPROVED))
            body = json.dumps({"options": [{"is_correct": True}, {"is_correct": False}]})
            self.variants.add_revision(NewRevision(name, "v1", body, state=RevisionState.APPROVED))
            stored = self.variants.get(name)
            assert stored is not None
            from dataclasses import replace

            self.variants.save(replace(stored, approved_content_version="v1"))

    def scan(self) -> ScanRefillParents:
        return ScanRefillParents(
            parents=self.parents,
            variants=self.variants,
            flags=self.flags,
            queue=self.queue,
            clock=self.clock,
        )


@settings(max_examples=40, deadline=None)
@given(
    specs=st.lists(
        st.tuples(st.booleans(), st.integers(min_value=0, max_value=THRESHOLD + 2)),
        min_size=0,
        max_size=6,
    )
)
def test_cycle_enqueues_iff_approved_and_below_threshold_once_per_bucket(
    specs: list[tuple[bool, int]],
) -> None:
    """The cycle enqueues exactly the Approved parents below the threshold, once per bucket."""
    parents = [_parent(f"p{i}", approved=ok) for i, (ok, _) in enumerate(specs)]
    world = _World(_Parents(*parents))
    for index, (_, count) in enumerate(specs):
        world.approve(f"p{index}", count)
    expected = sorted(f"p{i}" for i, (ok, count) in enumerate(specs) if ok and count < THRESHOLD)

    scan = world.scan()
    first = scan.execute()
    again = scan.execute()

    queued = [j.payload["parent_stable_id"] for j in world.queue.pending_for(REFILL_PARENT_JOB)]
    assert sorted(first) == expected
    assert sorted(str(q) for q in queued) == expected  # the repeat in the same bucket added nothing
    assert len(world.queue.pending_for(REFILL_PARENT_JOB)) == len(expected)
    assert again == first or len(queued) == len(expected)

    world.clock.advance(cfg.REFILL_CYCLE_INTERVAL + timedelta(seconds=1))
    scan.execute()
    assert len(world.queue.pending_for(REFILL_PARENT_JOB)) == 2 * len(expected)


@settings(max_examples=40, deadline=None)
@given(
    approved=st.integers(min_value=0, max_value=THRESHOLD - 1),
    stop=st.sampled_from(
        [BudgetReachedError("daily"), ProviderUnavailableError(), MalformedOutputError()]
    ),
    data=st.data(),
)
def test_a_stopped_refill_serves_nothing_and_enqueues_nothing(
    approved: int, stop: Exception, data: st.DataObject
) -> None:
    """After a stop, the Approved count is unchanged and nothing new is enqueued."""
    shortfall = min(THRESHOLD - approved, CAP)
    good = data.draw(st.integers(min_value=0, max_value=shortfall - 1))
    world = _World(_Parents(_parent("q1")), [_draft(i) for i in range(good)] + [stop])
    world.approve("q1", approved)

    world.worker.consumers[REFILL_PARENT_JOB]({"parent_stable_id": "q1"})

    assert world.variants.count_approved("q1") == approved  # nothing became servable
    assert world.queue.pending == ()  # no job until the next bucket
    assert len(world.tasks.page([TRACK], None, 50).items) <= good
