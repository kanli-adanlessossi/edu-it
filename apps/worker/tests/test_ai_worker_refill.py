"""Tests for the AI worker: refill jobs, composition and approved-only serving (task 15.2).

Run against the in-memory ports and a scripted model. No model is called and no network is used.

**Validates: Requirements 13.1, 13.5, 13.8, 13.9, 13.10**
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from edu_it_application.ai import NoServableQuestionError
from edu_it_domain import config as cfg
from edu_it_domain.ai.lifecycle import VariantState
from edu_it_domain.config import AI_CONFIG_KEYS, AiConfigError
from edu_it_domain.learning.models import QuestionOption
from edu_it_domain.ports.catalog import QuestionRecord, QuizRecord
from edu_it_domain.ports.generation_ledger import JobStatus
from edu_it_domain.ports.item_flags import FlagReason, FlagReasonKind, ItemKey, ItemKind
from edu_it_domain.ports.llm_provider import (
    AttemptUsage,
    BudgetReachedError,
    LLMInput,
    LLMProvider,
    OutputSchema,
    PromptRef,
    StructuredOutput,
)
from edu_it_domain.ports.parent_questions import ParentQuestion
from edu_it_domain.ports.variants import NewRevision, NewVariant, RevisionState
from edu_it_infrastructure.adapters.parent_questions import Content_ParentQuestionSource
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
from edu_it_worker.ai.composition import AiWorker, build_ai_worker
from edu_it_worker.ai.jobs import (
    REFILL_CYCLE_JOB,
    REFILL_PARENT_JOB,
    ScanRefillParents,
    refill_bucket,
    refill_parent_dedupe_key,
    refill_parent_handler,
)

T0 = datetime(2025, 1, 1, tzinfo=UTC)
TRACK = "track-1"


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


def _question(stable_id: str) -> QuestionRecord:
    return QuestionRecord(
        stable_id, (QuestionOption(1, True), QuestionOption(2, False)), ("con-1",)
    )


class _World:
    def __init__(
        self,
        parents: _Parents,
        script: list[str | Exception] | None = None,
        *,
        questions: tuple[str, ...] = ("q1",),
        ai_config: object = cfg,
    ) -> None:
        self.clock = Test_Clock(T0)
        self.queue = In_Memory_TaskQueue()
        self.ledger = In_Memory_GenerationLedger(self.clock)
        self.variants = In_Memory_VariantRepository(self.clock)
        self.tasks = In_Memory_ReviewTaskRepository(self.clock, self.variants)
        self.flags = In_Memory_ItemFlagRepository()
        self.parents = parents
        self.llm = _ScriptedLLM(script or [])
        self.scopes: list[JobScope] = []
        self.worker: AiWorker = build_ai_worker(
            queue=self.queue,
            clock=self.clock,
            ids=Sequential_IdGenerator("id"),
            ledger=self.ledger,
            parents=parents,
            variants=self.variants,
            review_tasks=self.tasks,
            flags=self.flags,
            catalog=_Catalog([_question(q) for q in questions]),
            sandbox=Fake_SandboxProvider(self.clock),
            settings=CompositionSettings(),
            wire=self._wire,
            ai_config=ai_config,
        )

    def _wire(self, scope: JobScope) -> tuple[LLMProvider, LLMProvider]:
        self.scopes.append(scope)
        answering = Fake_LLMProvider({"verify_answer": json.dumps({"selected": [0]})})
        return self.llm, answering

    def add_variant(self, name: str, state: VariantState, parent: str = "q1") -> None:
        self.variants.add(NewVariant(name, parent, TRACK, state))
        body = json.dumps({"options": [{"is_correct": True}, {"is_correct": False}]})
        self.variants.add_revision(NewRevision(name, "v1", body, state=RevisionState.APPROVED))
        stored = self.variants.get(name)
        assert stored is not None
        self.variants.save(replace(stored, approved_content_version="v1"))


def _approve(world: _World, count: int, parent: str = "q1") -> None:
    for index in range(count):
        world.add_variant(f"{parent}-v{index}", VariantState.APPROVED, parent)


# -- ai.refill.cycle ------------------------------------------------------------------------


def test_cycle_enqueues_exactly_the_approved_unflagged_parents_below_threshold() -> None:
    world = _World(
        _Parents(_parent("a"), _parent("b"), _parent("c"), _parent("d", approved=False)),
        questions=("a",),
    )
    _approve(world, cfg.VARIANT_POOL_THRESHOLD, "b")  # full
    world.flags.flag(ItemKey(ItemKind.QUESTION, "c"), FlagReason(FlagReasonKind.REPORTS, "x"))

    world.worker.consumers[REFILL_CYCLE_JOB]({})

    queued = [job.payload["parent_stable_id"] for job in world.queue.pending_for(REFILL_PARENT_JOB)]
    assert queued == ["a"]


def test_cycle_in_the_same_bucket_enqueues_nothing_new_and_the_next_bucket_does() -> None:
    world = _World(_Parents(_parent("a")))
    scan = ScanRefillParents(
        parents=world.parents,
        variants=world.variants,
        flags=world.flags,
        queue=world.queue,
        clock=world.clock,
    )

    scan.execute()
    scan.execute()
    assert len(world.queue.pending_for(REFILL_PARENT_JOB)) == 1
    key = refill_parent_dedupe_key("a", refill_bucket(world.clock))
    assert world.queue.pending_for(REFILL_PARENT_JOB)[0].dedupe_key == key

    world.clock.advance(cfg.REFILL_CYCLE_INTERVAL + timedelta(seconds=1))
    scan.execute()
    assert len(world.queue.pending_for(REFILL_PARENT_JOB)) == 2


def test_scheduler_fires_the_cycle_once_per_bucket() -> None:
    world = _World(_Parents())

    assert world.worker.scheduler.tick() == [REFILL_CYCLE_JOB]
    assert world.worker.scheduler.tick() == []
    pending = world.queue.pending_for(REFILL_CYCLE_JOB)
    assert [job.dedupe_key for job in pending] == [
        f"{REFILL_CYCLE_JOB}:tick:{refill_bucket(world.clock)}"
    ]


# -- ai.refill.parent -----------------------------------------------------------------------


def test_parent_job_stores_pending_variants_under_a_ledger_job() -> None:
    world = _World(_Parents(_parent("q1")), [_draft(1), _draft(2), _draft(3)])

    world.worker.consumers[REFILL_PARENT_JOB]({"parent_stable_id": "q1"})

    assert len(world.scopes) == 2  # preflight, then the job
    assert world.scopes[1].track_stable_id == TRACK
    assert world.variants.count_approved("q1") == 0  # nothing is servable until approved
    assert len(world.tasks.page([TRACK], None, 50).items) == 3
    assert world.queue.pending == ()


def test_budget_stop_creates_nothing_enqueues_nothing_and_closes_the_job() -> None:
    world = _World(_Parents(_parent("q1")), [BudgetReachedError("daily")])

    world.worker.consumers[REFILL_PARENT_JOB]({"parent_stable_id": "q1"})

    assert world.variants.parents_below(10) == []
    assert world.tasks.page([TRACK], None, 50).items == ()
    assert world.queue.pending == ()  # no re-enqueue before the next cycle (13.9)
    job = world.scopes[1].job
    assert world.ledger._job(job).status is JobStatus.BUDGET_REACHED


def test_unknown_parent_and_malformed_payload_are_swallowed() -> None:
    world = _World(_Parents(_parent("q1", approved=False)))
    handler = world.worker.consumers[REFILL_PARENT_JOB]

    handler({"parent_stable_id": "q1"})  # not Approved: no job opened
    handler({"parent_stable_id": "nope"})
    handler({})

    assert len(world.scopes) == 1  # the preflight only


def test_handler_logs_and_swallows_unexpected_errors() -> None:
    class _Boom:
        def execute(self, parent_stable_id: str) -> None:
            raise RuntimeError("boom")

    handler = refill_parent_handler(_Boom())  # type: ignore[arg-type]

    handler({"parent_stable_id": "q1"})  # does not raise


# -- composition ----------------------------------------------------------------------------


def test_composition_refuses_an_invalid_ai_config_naming_the_key() -> None:
    config = {k: getattr(cfg, k) for k in AI_CONFIG_KEYS}
    config["AI_RETENTION_AUDIT"] = timedelta(0)

    with pytest.raises(AiConfigError) as caught:
        _World(_Parents(), ai_config=SimpleNamespace(**config))

    assert "AI_RETENTION_AUDIT" in caught.value.keys


def test_start_runs_the_cycle_end_to_end_and_stop_joins_the_threads() -> None:
    world = _World(_Parents(_parent("q1")), [_draft(1), _draft(2), _draft(3)])

    world.worker.start()  # registers both consumers; the scheduler may already have ticked
    try:
        world.worker.scheduler.tick()
        for _ in range(3):
            world.queue.run_pending()  # cycle -> parent job -> variants
    finally:
        world.worker.stop()

    assert len(world.tasks.page([TRACK], None, 50).items) == 3
    assert world.worker.threads == []


# -- approved-only serving (Req 14.1, 14.5, 20.4) -------------------------------------------


def test_worker_serves_only_approved_unflagged_items_with_variant_ref() -> None:
    world = _World(_Parents(), questions=("q1", "q2"))
    world.add_variant("v-ok", VariantState.APPROVED)
    for state in (VariantState.PENDING_REVIEW, VariantState.FLAGGED, VariantState.WITHDRAWN):
        world.add_variant(f"v-{state.value}", state)
    world.flags.flag(ItemKey(ItemKind.QUESTION, "q2"), FlagReason(FlagReasonKind.REPORTS, "x"))
    servable = world.worker.servable

    assert [q.stable_id for q in servable.list_questions_for_concept("con-1")] == ["q1"]
    resolved = servable.resolve_review_question(
        concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
    )
    assert resolved.variant_ref is not None
    assert resolved.variant_ref.startswith("v-ok")
    assert resolved.question.stable_id == "q1"  # shown under the parent's stable_id


def test_worker_review_falls_back_to_the_parent_then_refuses_when_nothing_is_servable() -> None:
    world = _World(_Parents(), questions=("q1",))
    world.add_variant("v-pending", VariantState.PENDING_REVIEW)
    servable = world.worker.servable

    resolved = servable.resolve_review_question(
        concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
    )
    assert resolved.variant_ref is None

    world.flags.flag(ItemKey(ItemKind.QUESTION, "q1"), FlagReason(FlagReasonKind.REPORTS, "x"))
    with pytest.raises(NoServableQuestionError):
        servable.resolve_review_question(
            concept_id="con-1", parent_question_id="q1", user_ref="u", due_at=T0
        )


# -- Content_ParentQuestionSource -----------------------------------------------------------


class _Item:
    def __init__(self, stable_id: str, payload: dict[str, object], status: str = "active") -> None:
        self.stable_id = stable_id
        self.payload = json.dumps(payload)
        self.status = status


class _Store:
    def __init__(self, items: dict[str, list[_Item]], reviewed: set[str]) -> None:
        self._items = items
        self._reviewed = reviewed

    def list_items(self, content_type: str) -> list[_Item]:
        return self._items.get(content_type, [])

    def review_of(self, stable_id: str) -> tuple[str, datetime] | None:
        return ("rev", T0) if stable_id in self._reviewed else None


def _store(*, reviewed: set[str], archived: bool = False) -> _Store:
    def question(sid: str, origin: str) -> _Item:
        return _Item(
            sid,
            {
                "quiz_stable_id": "quiz.perm",
                "prompt_text": {"en": "Q?", "fr": "Q ?"},
                "provenance": {"origin": origin},
            },
            "archived" if archived else "active",
        )

    def option(sid: str, text: str, correct: bool) -> _Item:
        return _Item(
            sid,
            {
                "question_stable_id": "question.1",
                "text": {"en": text, "fr": text},
                "is_correct": correct,
            },
        )

    return _Store(
        {
            "question": [question("question.1", "ai"), question("question.2", "human")],
            "question_option": [option("o.1", "ls", True), option("o.2", "rm", False)],
            "quiz": [_Item("quiz.perm", {"lesson_stable_id": "lesson.1"})],
            "lesson": [_Item("lesson.1", {"module_stable_id": "module.1"})],
            "module": [_Item("module.1", {"track_stable_id": "track.1"})],
            "concept": [_Item("concept.perm", {"title": {"en": "Permissions", "fr": "Droits"}})],
        },
        reviewed,
    )


def test_content_source_describes_a_reviewed_question_without_the_key() -> None:
    source = Content_ParentQuestionSource(_store(reviewed={"question.1"}))

    parent = source.get("question.1")

    assert parent is not None
    assert parent.approved
    assert (parent.track_stable_id, parent.concept_stable_id) == ("track.1", "concept.perm")
    assert parent.concept_title == "Permissions"
    assert parent.options == ("ls", "rm")
    assert source.approved_ids() == ["question.1", "question.2"]  # question.2 is human


def test_content_source_does_not_approve_an_unreviewed_ai_or_archived_question() -> None:
    unreviewed = Content_ParentQuestionSource(_store(reviewed=set()))
    archived = Content_ParentQuestionSource(_store(reviewed={"question.1"}, archived=True))

    assert unreviewed.approved_ids() == ["question.2"]
    assert archived.approved_ids() == []
    assert unreviewed.get("missing") is None
